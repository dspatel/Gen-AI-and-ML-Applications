from __future__ import annotations

import argparse
import math
import os
import sqlite3
import time
from datetime import time as dt_time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
from zoneinfo import ZoneInfo
try:
    from tvDatafeed import Interval, TvDatafeed
except Exception:
    Interval = None
    TvDatafeed = None


SESSION_TZ = ZoneInfo("America/New_York")
SESSION_START = (9, 30)
SESSION_END = (16, 0)
OR_MINUTES = 30
FIRST_HALF_MINUTES = 195
CACHE_DB_DEFAULT = str(Path(__file__).with_name("data") / "market_data_cache.sqlite")


def _parse_hhmm(hhmm: str) -> dt_time:
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM time string: {hhmm!r}")
    h = int(parts[0])
    m = int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Invalid HH:MM time string: {hhmm!r}")
    return dt_time(hour=h, minute=m)


def _parse_period_days(period: str) -> Optional[int]:
    p = period.strip().lower()
    if p.endswith("d") and p[:-1].isdigit():
        return int(p[:-1])
    return None


def _parse_symbol_map(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not s.strip():
        return out
    for token in s.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"Invalid symbol map item '{token}'. Use SYMBOL:VALUE.")
        k, v = token.split(":", 1)
        out[k.strip().upper()] = v.strip().upper()
    return out


def _load_symbols_file(path: str) -> tuple[list[str], dict[str, str]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Symbols file not found: {path}")

    symbols: list[str] = []
    exch_map: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        sym = ""
        exch = ""
        if ":" in line:
            sym, exch = [x.strip() for x in line.split(":", 1)]
        elif "," in line:
            parts = [x.strip() for x in line.split(",", 1)]
            sym = parts[0]
            exch = parts[1] if len(parts) > 1 else ""
        else:
            sym = line.strip()

        sym = sym.upper()
        if not sym:
            continue
        symbols.append(sym)
        if exch:
            exch_map[sym] = exch.upper()

    # preserve order, de-duplicate
    seen = set()
    uniq = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq, exch_map


def _resolve_symbols_and_exchange_map(args: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    base_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    base_map = _parse_symbol_map(args.tv_exchanges)
    if getattr(args, "symbols_file", "").strip():
        file_symbols, file_map = _load_symbols_file(args.symbols_file)
        merged = dict(base_map)
        merged.update(file_map)
        return file_symbols, merged
    return base_symbols, base_map


def _cache_source_key(
    *,
    data_source: str,
    symbol: str,
    tv_exchange_map: dict[str, str],
    default_exchange: str,
    alpaca_feed: str,
) -> str:
    if data_source == "tvdatafeed":
        exch = tv_exchange_map.get(symbol.upper(), default_exchange.upper())
        return f"tvdatafeed:{exch}"
    if data_source == "alpaca":
        return f"alpaca:{alpaca_feed.lower()}"
    return "yfinance"


def _ensure_cache_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_bars (
            source_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            bar_interval TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY (source_key, symbol, bar_interval, ts_utc)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_bars_lookup
        ON market_bars (source_key, symbol, bar_interval, ts_utc)
        """
    )


def _cache_load_bars(
    cache_db: str,
    source_key: str,
    symbol: str,
    interval: str,
) -> pd.DataFrame:
    p = Path(cache_db)
    if not p.exists():
        return pd.DataFrame()

    with sqlite3.connect(str(p)) as conn:
        _ensure_cache_schema(conn)
        rows = conn.execute(
            """
            SELECT ts_utc, open, high, low, close, volume
            FROM market_bars
            WHERE source_key = ? AND symbol = ? AND bar_interval = ?
            ORDER BY ts_utc ASC
            """,
            (source_key, symbol.upper(), interval),
        ).fetchall()

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows, columns=["ts_utc", "open", "high", "low", "close", "volume"])
    t = pd.to_datetime(out["ts_utc"], utc=True).dt.tz_convert(SESSION_TZ)
    out["time_local"] = t
    return out[["time_local", "open", "high", "low", "close", "volume"]]


def _cache_store_bars(
    cache_db: str,
    source_key: str,
    symbol: str,
    interval: str,
    bars: pd.DataFrame,
) -> None:
    if bars.empty:
        return

    p = Path(cache_db)
    p.parent.mkdir(parents=True, exist_ok=True)
    now_utc = pd.Timestamp.now(tz="UTC").isoformat()
    ts_utc = bars["time_local"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%S%z")

    rows = list(
        zip(
            [source_key] * len(bars),
            [symbol.upper()] * len(bars),
            [interval] * len(bars),
            ts_utc.tolist(),
            bars["open"].astype(float).tolist(),
            bars["high"].astype(float).tolist(),
            bars["low"].astype(float).tolist(),
            bars["close"].astype(float).tolist(),
            bars["volume"].astype(float).tolist(),
            [now_utc] * len(bars),
        )
    )

    with sqlite3.connect(str(p)) as conn:
        _ensure_cache_schema(conn)
        conn.executemany(
            """
            INSERT INTO market_bars (
                source_key, symbol, bar_interval, ts_utc,
                open, high, low, close, volume, fetched_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, symbol, bar_interval, ts_utc)
            DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                fetched_at_utc = excluded.fetched_at_utc
            """
            ,
            rows,
        )
        conn.commit()


def _cache_satisfies_period(cached_bars: pd.DataFrame, period_days: Optional[int]) -> bool:
    if cached_bars.empty or period_days is None:
        return False
    dates = cached_bars["time_local"].dt.date.astype(str).unique()
    return len(dates) >= period_days


def _tv_interval_from_str(interval: str):
    if Interval is None:
        raise RuntimeError("tvDatafeed is not installed. Install from GitHub to use --data-source tvdatafeed.")

    m = {
        "1m": "in_1_minute",
        "3m": "in_3_minute",
        "5m": "in_5_minute",
        "15m": "in_15_minute",
        "30m": "in_30_minute",
        "45m": "in_45_minute",
        "1h": "in_1_hour",
        "2h": "in_2_hour",
        "3h": "in_3_hour",
        "4h": "in_4_hour",
        "1d": "in_daily",
        "1w": "in_weekly",
        "1mo": "in_monthly",
    }
    key = interval.strip().lower()
    if key not in m:
        raise ValueError(f"Unsupported tvdatafeed interval '{interval}'.")
    return getattr(Interval, m[key])


def _alpaca_timeframe_from_str(interval: str) -> str:
    m = {
        "1m": "1Min",
        "5m": "5Min",
        "15m": "15Min",
        "30m": "30Min",
        "1h": "1Hour",
        "1d": "1Day",
    }
    key = interval.strip().lower()
    if key not in m:
        raise ValueError(
            f"Unsupported Alpaca timeframe '{interval}'. "
            f"Use one of: {', '.join(sorted(m.keys()))}."
        )
    return m[key]


def _fetch_alpaca_bars(
    symbol: str,
    interval: str,
    period: str,
    alpaca_key: str,
    alpaca_secret: str,
    alpaca_feed: str,
    alpaca_base_url: str,
) -> pd.DataFrame:
    key = alpaca_key.strip() or os.getenv("APCA_API_KEY_ID", "").strip()
    secret = alpaca_secret.strip() or os.getenv("APCA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError(
            "Alpaca credentials missing. Set APCA_API_KEY_ID/APCA_API_SECRET_KEY "
            "or pass --alpaca-key/--alpaca-secret."
        )

    period_days = _parse_period_days(period) or 60
    # Convert trading-day request into a conservative calendar window.
    calendar_days = max(period_days * 2, 30)
    end_utc = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    start_utc = end_utc - pd.Timedelta(days=calendar_days)

    timeframe = _alpaca_timeframe_from_str(interval)
    base = alpaca_base_url.rstrip("/")
    url = f"{base}/v2/stocks/bars"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }

    rows: list[dict] = []
    page_token: Optional[str] = None
    while True:
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start_utc.isoformat(),
            "end": end_utc.isoformat(),
            "limit": 10000,
            "adjustment": "raw",
            "feed": alpaca_feed,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Alpaca API error for {symbol}: {resp.status_code} {resp.text[:300]}")

        payload = resp.json()
        bars = payload.get("bars", {}).get(symbol, []) or []
        rows.extend(bars)
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    required = {"t", "o", "h", "l", "c", "v"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{symbol}: missing Alpaca columns {missing}")

    t = pd.to_datetime(df["t"], utc=True).dt.tz_convert(SESSION_TZ)
    out = pd.DataFrame(
        {
            "time_local": t,
            "open": df["o"].astype(float),
            "high": df["h"].astype(float),
            "low": df["l"].astype(float),
            "close": df["c"].astype(float),
            "volume": df["v"].astype(float),
        }
    )
    out = out.sort_values("time_local").drop_duplicates(subset=["time_local"]).reset_index(drop=True)
    return out


def _fetch_tvdatafeed_bars(
    tv: "TvDatafeed",
    symbol: str,
    exchange: str,
    interval: str,
    n_bars: int,
) -> pd.DataFrame:
    iv = _tv_interval_from_str(interval)
    raw = tv.get_hist(symbol=symbol, exchange=exchange, interval=iv, n_bars=n_bars)
    if raw is None or raw.empty:
        return pd.DataFrame()

    out = raw.copy().reset_index()
    if "datetime" not in out.columns:
        raise RuntimeError(f"{symbol}: tvdatafeed response missing datetime column.")

    t = pd.to_datetime(out["datetime"])
    if t.dt.tz is None:
        # tvdatafeed returns exchange-local timestamps as naive for US symbols.
        t = t.dt.tz_localize(SESSION_TZ)
    else:
        t = t.dt.tz_convert(SESSION_TZ)

    out["time_local"] = t

    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise RuntimeError(f"{symbol}: missing columns from tvdatafeed: {missing}")

    out = out[["time_local", "open", "high", "low", "close", "volume"]].copy()
    out = out.sort_values("time_local").drop_duplicates(subset=["time_local"]).reset_index(drop=True)
    return out


def _normalize_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex):
        if symbol in out.columns.get_level_values(-1):
            out = out.xs(symbol, axis=1, level=-1, drop_level=True)
        elif symbol in out.columns.get_level_values(0):
            out = out.xs(symbol, axis=1, level=0, drop_level=True)

    out = out.reset_index()

    if "Datetime" in out.columns:
        out = out.rename(columns={"Datetime": "time"})
    elif "Date" in out.columns:
        out = out.rename(columns={"Date": "time"})

    out = out.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise RuntimeError(f"{symbol}: missing columns after normalization: {missing}")

    t = pd.to_datetime(out["time"])
    if t.dt.tz is None:
        t = t.dt.tz_localize("UTC")

    out["time_local"] = t.dt.tz_convert(SESSION_TZ)
    out = out.sort_values("time_local").reset_index(drop=True)
    return out[["time_local", "open", "high", "low", "close", "volume"]]


def _regular_session_only(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    local_day = df["time_local"].dt.floor("D")
    start = local_day + pd.Timedelta(hours=SESSION_START[0], minutes=SESSION_START[1])
    end = local_day + pd.Timedelta(hours=SESSION_END[0], minutes=SESSION_END[1])
    out = df[(df["time_local"] >= start) & (df["time_local"] < end)].copy()
    return out.reset_index(drop=True)


def _load_market_data(
    symbols: list[str],
    period: str,
    interval: str,
    data_source: str,
    tv_n_bars: int,
    tv_username: str,
    tv_password: str,
    tv_exchange_map: dict[str, str],
    default_exchange: str,
    alpaca_key: str,
    alpaca_secret: str,
    alpaca_feed: str,
    alpaca_base_url: str,
    cache_db: str = CACHE_DB_DEFAULT,
    cache_disabled: bool = False,
    cache_refresh: bool = False,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    dates_per_symbol: dict[str, set[str]] = {}
    period_days = _parse_period_days(period)
    tv_client: Optional["TvDatafeed"] = None

    if data_source == "tvdatafeed":
        if TvDatafeed is None:
            raise RuntimeError("tvDatafeed is not installed. Install from GitHub to use --data-source tvdatafeed.")
        tv_client = TvDatafeed(
            username=tv_username if tv_username else None,
            password=tv_password if tv_password else None,
        )

    for sym in symbols:
        source_key = _cache_source_key(
            data_source=data_source,
            symbol=sym,
            tv_exchange_map=tv_exchange_map,
            default_exchange=default_exchange,
            alpaca_feed=alpaca_feed,
        )
        bars = pd.DataFrame()

        if not cache_disabled and not cache_refresh:
            cached = _cache_load_bars(
                cache_db=cache_db,
                source_key=source_key,
                symbol=sym,
                interval=interval,
            )
            if _cache_satisfies_period(cached_bars=cached, period_days=period_days):
                bars = cached

        if bars.empty:
            if data_source == "tvdatafeed":
                exchange = tv_exchange_map.get(sym.upper(), default_exchange.upper())
                for attempt in range(3):
                    try:
                        if attempt > 0:
                            tv_client = TvDatafeed(
                                username=tv_username if tv_username else None,
                                password=tv_password if tv_password else None,
                            )
                        bars = _fetch_tvdatafeed_bars(
                            tv=tv_client,
                            symbol=sym,
                            exchange=exchange,
                            interval=interval,
                            n_bars=tv_n_bars,
                        )
                    except Exception:
                        bars = pd.DataFrame()
                    if not bars.empty:
                        break
                    time.sleep(1.0)
            elif data_source == "yfinance":
                raw = yf.download(
                    tickers=sym,
                    interval=interval,
                    period=period,
                    auto_adjust=False,
                    prepost=False,
                    progress=False,
                    threads=False,
                )
                bars = _normalize_ohlcv(raw, sym)
            elif data_source == "alpaca":
                bars = _fetch_alpaca_bars(
                    symbol=sym,
                    interval=interval,
                    period=period,
                    alpaca_key=alpaca_key,
                    alpaca_secret=alpaca_secret,
                    alpaca_feed=alpaca_feed,
                    alpaca_base_url=alpaca_base_url,
                )
            else:
                raise ValueError(f"Unsupported data source '{data_source}'.")

            bars = _regular_session_only(bars)
            if not cache_disabled and not bars.empty:
                _cache_store_bars(
                    cache_db=cache_db,
                    source_key=source_key,
                    symbol=sym,
                    interval=interval,
                    bars=bars,
                )
        else:
            bars = _regular_session_only(bars)

        if bars.empty:
            raise RuntimeError(f"No regular-session bars returned for {sym}.")

        bars["session_date"] = bars["time_local"].dt.date.astype(str)
        if data_source in {"tvdatafeed", "alpaca"} and period_days is not None:
            unique_dates = sorted(bars["session_date"].unique())
            keep_dates = set(unique_dates[-period_days:])
            bars = bars[bars["session_date"].isin(keep_dates)].reset_index(drop=True)
            if bars.empty:
                raise RuntimeError(f"{sym}: no rows after applying period={period} window to {data_source} bars.")

        bars_by_symbol[sym] = bars
        dates_per_symbol[sym] = set(bars["session_date"].unique())

    common_dates = sorted(set.intersection(*dates_per_symbol.values()))
    if not common_dates:
        raise RuntimeError("No common trading dates found across all symbols.")

    return bars_by_symbol, common_dates


def _add_intraday_features(day_df: pd.DataFrame, rvol_lookback_bars: int) -> pd.DataFrame:
    out = day_df.copy()
    out["cum_pv"] = (out["close"] * out["volume"]).cumsum()
    out["cum_vol"] = out["volume"].cumsum()
    out["vwap"] = out["cum_pv"] / out["cum_vol"].replace(0, pd.NA)
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema20_slope"] = out["ema20"].diff()

    if rvol_lookback_bars > 0:
        out["avg_vol_prev"] = out["volume"].rolling(rvol_lookback_bars, min_periods=rvol_lookback_bars).mean().shift(1)
        out["rvol"] = out["volume"] / out["avg_vol_prev"]
    else:
        out["avg_vol_prev"] = pd.NA
        out["rvol"] = pd.NA

    return out


def _detect_first_confirmed_breakout(
    day_df: pd.DataFrame,
    direction_mode: str,
    min_breakout_frac: float,
    min_rvol: float,
    rvol_lookback_bars: int,
    use_vwap_filter: bool,
    use_ema_slope_filter: bool,
    entry_cutoff_hhmm: str,
) -> Optional[dict]:
    if day_df.empty:
        return None

    work = _add_intraday_features(day_df, rvol_lookback_bars)

    session_start = work.iloc[0]["time_local"].normalize() + pd.Timedelta(
        hours=SESSION_START[0], minutes=SESSION_START[1]
    )
    or_end = session_start + pd.Timedelta(minutes=OR_MINUTES)
    first_half_end_dt = session_start + pd.Timedelta(minutes=FIRST_HALF_MINUTES)

    if entry_cutoff_hhmm.strip():
        cutoff_t = _parse_hhmm(entry_cutoff_hhmm)
        cutoff_dt = session_start.normalize() + pd.Timedelta(hours=cutoff_t.hour, minutes=cutoff_t.minute)
    else:
        cutoff_dt = first_half_end_dt

    effective_cutoff = min(first_half_end_dt, cutoff_dt)

    orb = work[(work["time_local"] >= session_start) & (work["time_local"] < or_end)]
    if orb.empty:
        return None

    or_high = float(orb["high"].max())
    or_low = float(orb["low"].min())
    or_width = max(or_high - or_low, 1e-9)
    start_idx = int(len(orb))

    if len(work) < start_idx + 2:
        return None

    i = start_idx
    while i < len(work) - 1:
        b = work.iloc[i]
        c = work.iloc[i + 1]

        confirm_dt = c["time_local"]
        if confirm_dt > effective_cutoff:
            break

        b_close = float(b["close"])
        c_close = float(c["close"])

        direction = None
        boundary = None

        if b_close > or_high and c_close > b_close:
            direction = "UP_TRUE"
            boundary = or_high
        elif b_close < or_low and c_close < b_close:
            direction = "DOWN_TRUE"
            boundary = or_low
        else:
            i += 1
            continue

        if direction_mode == "up" and direction != "UP_TRUE":
            i += 1
            continue
        if direction_mode == "down" and direction != "DOWN_TRUE":
            i += 1
            continue

        breakout_gap = abs(b_close - float(boundary))
        if breakout_gap < (min_breakout_frac * or_width):
            i += 1
            continue

        if min_rvol > 0:
            rvol = c.get("rvol", pd.NA)
            if pd.isna(rvol) or float(rvol) < min_rvol:
                i += 1
                continue

        if use_vwap_filter:
            vwap = c.get("vwap", pd.NA)
            if pd.isna(vwap):
                i += 1
                continue
            vwap_val = float(vwap)
            if direction == "UP_TRUE" and c_close <= vwap_val:
                i += 1
                continue
            if direction == "DOWN_TRUE" and c_close >= vwap_val:
                i += 1
                continue

        if use_ema_slope_filter:
            ema = c.get("ema20", pd.NA)
            slope = c.get("ema20_slope", pd.NA)
            if pd.isna(ema) or pd.isna(slope):
                i += 1
                continue
            ema_val = float(ema)
            slope_val = float(slope)
            if direction == "UP_TRUE" and not (c_close > ema_val and slope_val > 0):
                i += 1
                continue
            if direction == "DOWN_TRUE" and not (c_close < ema_val and slope_val < 0):
                i += 1
                continue

        return {
            "direction": direction,
            "entry_idx": i + 1,
            "entry_time": confirm_dt,
            "entry_price": c_close,
            "or_width": or_width,
            "confirm_rvol": None if pd.isna(c.get("rvol", pd.NA)) else float(c["rvol"]),
        }

        i += 1

    return None


def _resolve_exit(
    day_df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    entry_price: float,
    exit_mode: str,
    or_width: float,
    stop_or_mult: float,
    target_or_mult: float,
    time_stop_bars: int,
    min_progress_r: float,
    break_even_r: float,
    trail_mode: str,
    trail_after_r: float,
) -> dict:
    close_price = float(day_df.iloc[-1]["close"])
    close_time = day_df.iloc[-1]["time_local"]

    if exit_mode == "close":
        return {"exit_price": close_price, "exit_time": close_time, "exit_reason": "close"}

    if entry_idx >= len(day_df) - 1:
        return {"exit_price": close_price, "exit_time": close_time, "exit_reason": "close"}

    work = day_df.copy()
    if trail_mode == "ema":
        work["trail_ref"] = work["close"].ewm(span=15, adjust=False).mean()
    elif trail_mode == "vwap":
        work["trail_ref"] = (work["close"] * work["volume"]).cumsum() / work["volume"].cumsum().replace(0, pd.NA)
    else:
        work["trail_ref"] = pd.NA

    risk = max(stop_or_mult * or_width, 1e-9)
    max_fav_r = 0.0
    break_even_armed = False
    trail_armed = False

    # Conservative intrabar assumption: if stop and target hit in same bar, stop fills first.
    if direction == "UP_TRUE":
        stop_px = entry_price - risk
        target_px = entry_price + target_or_mult * or_width

        for j in range(entry_idx + 1, len(work)):
            bar = work.iloc[j]
            lo = float(bar["low"])
            hi = float(bar["high"])
            c = float(bar["close"])
            bars_elapsed = j - entry_idx

            if lo <= stop_px:
                if trail_armed and stop_px >= entry_price:
                    r = "trail_stop"
                elif break_even_armed and stop_px >= entry_price:
                    r = "breakeven_stop"
                else:
                    r = "stop"
                return {"exit_price": stop_px, "exit_time": bar["time_local"], "exit_reason": r}
            if hi >= target_px:
                return {"exit_price": target_px, "exit_time": bar["time_local"], "exit_reason": "target"}

            max_fav_r = max(max_fav_r, (hi - entry_price) / risk)

            if break_even_r > 0 and max_fav_r >= break_even_r:
                break_even_armed = True
                stop_px = max(stop_px, entry_price)

            if trail_mode != "none" and trail_after_r > 0 and max_fav_r >= trail_after_r:
                tr = bar.get("trail_ref", pd.NA)
                if not pd.isna(tr):
                    trail_armed = True
                    stop_px = max(stop_px, float(tr))

            if time_stop_bars > 0 and bars_elapsed >= time_stop_bars and max_fav_r < min_progress_r:
                return {"exit_price": c, "exit_time": bar["time_local"], "exit_reason": "time_stop"}
    else:
        stop_px = entry_price + risk
        target_px = entry_price - target_or_mult * or_width

        for j in range(entry_idx + 1, len(work)):
            bar = work.iloc[j]
            lo = float(bar["low"])
            hi = float(bar["high"])
            c = float(bar["close"])
            bars_elapsed = j - entry_idx

            if hi >= stop_px:
                if trail_armed and stop_px <= entry_price:
                    r = "trail_stop"
                elif break_even_armed and stop_px <= entry_price:
                    r = "breakeven_stop"
                else:
                    r = "stop"
                return {"exit_price": stop_px, "exit_time": bar["time_local"], "exit_reason": r}
            if lo <= target_px:
                return {"exit_price": target_px, "exit_time": bar["time_local"], "exit_reason": "target"}

            max_fav_r = max(max_fav_r, (entry_price - lo) / risk)

            if break_even_r > 0 and max_fav_r >= break_even_r:
                break_even_armed = True
                stop_px = min(stop_px, entry_price)

            if trail_mode != "none" and trail_after_r > 0 and max_fav_r >= trail_after_r:
                tr = bar.get("trail_ref", pd.NA)
                if not pd.isna(tr):
                    trail_armed = True
                    stop_px = min(stop_px, float(tr))

            if time_stop_bars > 0 and bars_elapsed >= time_stop_bars and max_fav_r < min_progress_r:
                return {"exit_price": c, "exit_time": bar["time_local"], "exit_reason": "time_stop"}

    return {"exit_price": close_price, "exit_time": close_time, "exit_reason": "close"}


def run_shared_cash_backtest(
    symbols: list[str],
    period: str,
    interval: str,
    start_shares_each: int,
    start_cash_total: float,
    trade_fraction: float,
    data_source: str = "yfinance",
    tv_n_bars: int = 5000,
    tv_username: str = "",
    tv_password: str = "",
    tv_exchange_map: Optional[dict[str, str]] = None,
    tv_default_exchange: str = "NASDAQ",
    alpaca_key: str = "",
    alpaca_secret: str = "",
    alpaca_feed: str = "iex",
    alpaca_base_url: str = "https://data.alpaca.markets",
    cache_db: str = CACHE_DB_DEFAULT,
    cache_disabled: bool = False,
    cache_refresh: bool = False,
    direction_mode: str = "both",
    min_breakout_frac: float = 0.0,
    min_rvol: float = 0.0,
    rvol_lookback_bars: int = 6,
    use_vwap_filter: bool = False,
    use_ema_slope_filter: bool = False,
    entry_cutoff_hhmm: str = "",
    exit_mode: str = "close",
    stop_or_mult: float = 0.6,
    target_or_mult: float = 1.2,
    time_stop_bars: int = 0,
    min_progress_r: float = 0.3,
    break_even_r: float = 0.0,
    trail_mode: str = "none",
    trail_after_r: float = 1.0,
    bars_by_symbol: Optional[dict[str, pd.DataFrame]] = None,
    common_dates: Optional[list[str]] = None,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    if bars_by_symbol is None or common_dates is None:
        bars_by_symbol, common_dates = _load_market_data(
            symbols=symbols,
            period=period,
            interval=interval,
            data_source=data_source,
            tv_n_bars=tv_n_bars,
            tv_username=tv_username,
            tv_password=tv_password,
            tv_exchange_map=tv_exchange_map or {},
            default_exchange=tv_default_exchange,
            alpaca_key=alpaca_key,
            alpaca_secret=alpaca_secret,
            alpaca_feed=alpaca_feed,
            alpaca_base_url=alpaca_base_url,
            cache_db=cache_db,
            cache_disabled=cache_disabled,
            cache_refresh=cache_refresh,
        )

    shares = {sym: int(start_shares_each) for sym in symbols}
    cash = float(start_cash_total)

    first_date = common_dates[0]
    initial_equity = cash
    for sym in symbols:
        first_open = float(bars_by_symbol[sym][bars_by_symbol[sym]["session_date"] == first_date].iloc[0]["open"])
        initial_equity += shares[sym] * first_open

    trade_rows: list[dict] = []
    day_rows: list[dict] = []

    for date in common_dates:
        day_events: list[dict] = []
        close_px: dict[str, float] = {}

        for sym in symbols:
            day_df = bars_by_symbol[sym][bars_by_symbol[sym]["session_date"] == date].reset_index(drop=True)
            if day_df.empty:
                continue

            close_px[sym] = float(day_df.iloc[-1]["close"])
            sig = _detect_first_confirmed_breakout(
                day_df=day_df,
                direction_mode=direction_mode,
                min_breakout_frac=min_breakout_frac,
                min_rvol=min_rvol,
                rvol_lookback_bars=rvol_lookback_bars,
                use_vwap_filter=use_vwap_filter,
                use_ema_slope_filter=use_ema_slope_filter,
                entry_cutoff_hhmm=entry_cutoff_hhmm,
            )
            if sig is None:
                continue

            exit_info = _resolve_exit(
                day_df=day_df,
                entry_idx=int(sig["entry_idx"]),
                direction=str(sig["direction"]),
                entry_price=float(sig["entry_price"]),
                exit_mode=exit_mode,
                or_width=float(sig["or_width"]),
                stop_or_mult=stop_or_mult,
                target_or_mult=target_or_mult,
                time_stop_bars=time_stop_bars,
                min_progress_r=min_progress_r,
                break_even_r=break_even_r,
                trail_mode=trail_mode,
                trail_after_r=trail_after_r,
            )

            day_events.append(
                {
                    "session_date": date,
                    "symbol": sym,
                    "direction": sig["direction"],
                    "entry_time": sig["entry_time"],
                    "entry_price": float(sig["entry_price"]),
                    "exit_time": exit_info["exit_time"],
                    "exit_price": float(exit_info["exit_price"]),
                    "exit_reason": str(exit_info["exit_reason"]),
                    "close_price": close_px[sym],
                    "or_width": float(sig["or_width"]),
                    "confirm_rvol": sig["confirm_rvol"],
                }
            )

        day_events.sort(key=lambda x: (x["entry_time"], x["symbol"]))
        pending_closes: list[dict] = []

        def apply_due_closes(until_time: pd.Timestamp) -> None:
            nonlocal cash
            if not pending_closes:
                return
            pending_closes.sort(key=lambda x: (x["exit_time"], x["symbol"]))
            i = 0
            while i < len(pending_closes):
                pos = pending_closes[i]
                if pos["exit_time"] > until_time:
                    i += 1
                    continue
                cash += pos["cash_delta"]
                if pos["shares_restore"] > 0:
                    shares[pos["symbol"]] += pos["shares_restore"]
                pending_closes.pop(i)

        for ev in day_events:
            apply_due_closes(ev["entry_time"])

            sym = ev["symbol"]
            direction = ev["direction"]
            entry_price = ev["entry_price"]
            exit_price = ev["exit_price"]

            qty = 0
            pnl = 0.0
            cash_before = cash

            if direction == "UP_TRUE":
                budget = cash * trade_fraction
                qty = int(math.floor(budget / entry_price))
                if qty > 0:
                    cash -= qty * entry_price
                    pending_closes.append(
                        {
                            "symbol": sym,
                            "exit_time": ev["exit_time"],
                            "cash_delta": qty * exit_price,
                            "shares_restore": 0,
                        }
                    )
                    pnl = (exit_price - entry_price) * qty

            elif direction == "DOWN_TRUE":
                qty = int(math.floor(shares[sym] * trade_fraction))
                if qty > 0:
                    shares[sym] -= qty
                    cash += qty * entry_price
                    pending_closes.append(
                        {
                            "symbol": sym,
                            "exit_time": ev["exit_time"],
                            "cash_delta": -qty * exit_price,
                            "shares_restore": qty,
                        }
                    )
                    pnl = (entry_price - exit_price) * qty

            trade_rows.append(
                {
                    "session_date": ev["session_date"],
                    "symbol": sym,
                    "direction": direction,
                    "entry_time": ev["entry_time"],
                    "entry_price": entry_price,
                    "exit_time": ev["exit_time"],
                    "exit_price": exit_price,
                    "exit_reason": ev["exit_reason"],
                    "close_price": ev["close_price"],
                    "quantity": qty,
                    "cash_before": cash_before,
                    "cash_after_entry": cash,
                    "pnl": pnl,
                    "or_width": ev["or_width"],
                    "confirm_rvol": ev["confirm_rvol"],
                }
            )

        if pending_closes:
            latest_exit = max(p["exit_time"] for p in pending_closes)
            apply_due_closes(latest_exit)

        day_equity = cash + sum(shares[s] * close_px.get(s, 0.0) for s in symbols)
        day_rows.append(
            {
                "session_date": date,
                "cash_end": cash,
                "equity_end": day_equity,
                "trades_opened": int(len(day_events)),
                **{f"shares_{s}": shares[s] for s in symbols},
            }
        )

    trades_df = pd.DataFrame(trade_rows)
    days_df = pd.DataFrame(day_rows)

    if days_df.empty:
        raise RuntimeError("No day-level results produced.")

    final_equity = float(days_df.iloc[-1]["equity_end"])
    strategy_realized_pnl = float(trades_df.loc[trades_df["quantity"] > 0, "pnl"].sum()) if not trades_df.empty else 0.0

    final_closes = {s: float(bars_by_symbol[s].iloc[-1]["close"]) for s in symbols}
    buy_hold_final_equity = float(start_cash_total + sum(start_shares_each * final_closes[s] for s in symbols))
    buy_hold_pnl = float(buy_hold_final_equity - initial_equity)

    summary = {
        "symbols": ",".join(symbols),
        "period": period,
        "interval": interval,
        "data_source": data_source,
        "tv_n_bars": int(tv_n_bars) if data_source == "tvdatafeed" else "N/A",
        "alpaca_feed": alpaca_feed if data_source == "alpaca" else "N/A",
        "cache_db": cache_db if not cache_disabled else "OFF",
        "cache_refresh": bool(cache_refresh),
        "direction_mode": direction_mode,
        "min_breakout_frac": float(min_breakout_frac),
        "min_rvol": float(min_rvol),
        "use_vwap_filter": bool(use_vwap_filter),
        "use_ema_slope_filter": bool(use_ema_slope_filter),
        "entry_cutoff_hhmm": entry_cutoff_hhmm if entry_cutoff_hhmm.strip() else "FIRST_HALF_END",
        "exit_mode": exit_mode,
        "stop_or_mult": float(stop_or_mult),
        "target_or_mult": float(target_or_mult),
        "time_stop_bars": int(time_stop_bars),
        "min_progress_r": float(min_progress_r),
        "break_even_r": float(break_even_r),
        "trail_mode": trail_mode,
        "trail_after_r": float(trail_after_r),
        "trading_days": int(len(common_dates)),
        "days_with_any_signal": int(trades_df["session_date"].nunique()) if not trades_df.empty else 0,
        "trades_executed": int((trades_df["quantity"] > 0).sum()) if not trades_df.empty else 0,
        "initial_cash_total": float(start_cash_total),
        "final_cash_total": float(cash),
        "cash_change": float(cash - start_cash_total),
        "initial_equity_total": float(initial_equity),
        "final_equity_total": final_equity,
        "net_pnl_total": float(final_equity - initial_equity),
        "strategy_realized_pnl": strategy_realized_pnl,
        "buy_hold_pnl": buy_hold_pnl,
        "strategy_alpha_vs_buy_hold": float((final_equity - initial_equity) - buy_hold_pnl),
        "start_date": common_dates[0],
        "end_date": common_dates[-1],
    }
    for sym in symbols:
        summary[f"final_shares_{sym}"] = int(shares[sym])

    return trades_df, summary, days_df


def _run_experiments(args: argparse.Namespace) -> None:
    symbols, tv_exchange_map = _resolve_symbols_and_exchange_map(args)
    tv_username = args.tv_username or os.getenv("TVDATAFEED_USERNAME", "")
    tv_password = args.tv_password or os.getenv("TVDATAFEED_PASSWORD", "")
    alpaca_key = args.alpaca_key or os.getenv("APCA_API_KEY_ID", "")
    alpaca_secret = args.alpaca_secret or os.getenv("APCA_API_SECRET_KEY", "")

    bars_by_symbol, common_dates = _load_market_data(
        symbols=symbols,
        period=args.period,
        interval=args.interval,
        data_source=args.data_source,
        tv_n_bars=args.tv_n_bars,
        tv_username=tv_username,
        tv_password=tv_password,
        tv_exchange_map=tv_exchange_map,
        default_exchange=args.tv_default_exchange,
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
        alpaca_feed=args.alpaca_feed,
        alpaca_base_url=args.alpaca_base_url,
        cache_db=args.cache_db,
        cache_disabled=args.no_cache,
        cache_refresh=args.cache_refresh,
    )

    presets = [
        {
            "name": "baseline",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
        },
        {
            "name": "up_only",
            "direction_mode": "up",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
        },
        {
            "name": "down_only",
            "direction_mode": "down",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
        },
        {
            "name": "strength_0.15",
            "direction_mode": "both",
            "min_breakout_frac": 0.15,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
        },
        {
            "name": "rvol_1.3",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 1.3,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
        },
        {
            "name": "vwap_ema",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": True,
            "use_ema_slope_filter": True,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
        },
        {
            "name": "cutoff_11_00",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "11:00",
            "exit_mode": "close",
        },
        {
            "name": "combo_filtered",
            "direction_mode": "up",
            "min_breakout_frac": 0.15,
            "min_rvol": 1.3,
            "use_vwap_filter": True,
            "use_ema_slope_filter": True,
            "entry_cutoff_hhmm": "11:00",
            "exit_mode": "bracket",
        },
    ]

    rows: list[dict] = []
    for p in presets:
        _, summary, _ = run_shared_cash_backtest(
            symbols=symbols,
            period=args.period,
            interval=args.interval,
            start_shares_each=args.start_shares_each,
            start_cash_total=args.start_cash_total,
            trade_fraction=args.trade_fraction,
            data_source=args.data_source,
            tv_n_bars=args.tv_n_bars,
            tv_username=tv_username,
            tv_password=tv_password,
            tv_exchange_map=tv_exchange_map,
            tv_default_exchange=args.tv_default_exchange,
            alpaca_key=alpaca_key,
            alpaca_secret=alpaca_secret,
            alpaca_feed=args.alpaca_feed,
            alpaca_base_url=args.alpaca_base_url,
            cache_db=args.cache_db,
            cache_disabled=args.no_cache,
            cache_refresh=args.cache_refresh,
            direction_mode=p["direction_mode"],
            min_breakout_frac=p["min_breakout_frac"],
            min_rvol=p["min_rvol"],
            rvol_lookback_bars=args.rvol_lookback_bars,
            use_vwap_filter=p["use_vwap_filter"],
            use_ema_slope_filter=p["use_ema_slope_filter"],
            entry_cutoff_hhmm=p["entry_cutoff_hhmm"],
            exit_mode=p["exit_mode"],
            stop_or_mult=args.stop_or_mult,
            target_or_mult=args.target_or_mult,
            bars_by_symbol=bars_by_symbol,
            common_dates=common_dates,
        )
        rows.append(
            {
                "preset": p["name"],
                "cash_change": summary["cash_change"],
                "final_cash": summary["final_cash_total"],
                "strategy_pnl": summary["strategy_realized_pnl"],
                "net_pnl_total": summary["net_pnl_total"],
                "alpha_vs_bh": summary["strategy_alpha_vs_buy_hold"],
                "trades_executed": summary["trades_executed"],
                "days_with_signal": summary["days_with_any_signal"],
            }
        )

    df = pd.DataFrame(rows).sort_values("cash_change", ascending=False).reset_index(drop=True)
    print("=== ORB_TEST Shared-Cash Experiments ===")
    print(df.to_string(index=False))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Shared-cash ORB backtest across multiple symbols.")
    p.add_argument("--symbols", default="QQQ,NVDA,SPY", help="Comma-separated symbols")
    p.add_argument("--symbols-file", default="", help="Optional path to symbols file. Supports SYMBOL or SYMBOL:EXCHANGE per line.")
    p.add_argument("--data-source", choices=["yfinance", "tvdatafeed", "alpaca"], default="yfinance")
    p.add_argument("--period", default="60d")
    p.add_argument("--interval", default="15m")
    p.add_argument("--tv-n-bars", type=int, default=5000, help="Bars to request from tvdatafeed.")
    p.add_argument("--tv-exchanges", default="QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX", help="SYMBOL:EXCHANGE map for tvdatafeed.")
    p.add_argument("--tv-default-exchange", default="NASDAQ", help="Fallback exchange for symbols missing in --tv-exchanges.")
    p.add_argument("--tv-username", default="", help="TradingView username (optional). Can also use TVDATAFEED_USERNAME env var.")
    p.add_argument("--tv-password", default="", help="TradingView password (optional). Can also use TVDATAFEED_PASSWORD env var.")
    p.add_argument("--alpaca-key", default="", help="Alpaca API key. Can also use APCA_API_KEY_ID env var.")
    p.add_argument("--alpaca-secret", default="", help="Alpaca API secret. Can also use APCA_API_SECRET_KEY env var.")
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex", help="Alpaca data feed. Free tier typically uses iex.")
    p.add_argument("--alpaca-base-url", default="https://data.alpaca.markets")
    p.add_argument("--cache-db", default=CACHE_DB_DEFAULT, help="SQLite cache file for market bars.")
    p.add_argument("--cache-refresh", action="store_true", help="Force refresh from provider and overwrite cache rows.")
    p.add_argument("--no-cache", action="store_true", help="Disable cache reads/writes for this run.")
    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--start-cash-total", type=float, default=10000.0)
    p.add_argument("--trade-fraction", type=float, default=0.2)

    p.add_argument("--direction-mode", choices=["both", "up", "down"], default="both")
    p.add_argument("--min-breakout-frac", type=float, default=0.0)
    p.add_argument("--min-rvol", type=float, default=0.0)
    p.add_argument("--rvol-lookback-bars", type=int, default=6)
    p.add_argument("--use-vwap-filter", action="store_true")
    p.add_argument("--use-ema-slope-filter", action="store_true")
    p.add_argument("--entry-cutoff", default="", help="HH:MM ET cutoff for confirmation candle time (optional)")

    p.add_argument("--exit-mode", choices=["close", "bracket"], default="close")
    p.add_argument("--stop-or-mult", type=float, default=0.6)
    p.add_argument("--target-or-mult", type=float, default=1.2)
    p.add_argument("--time-stop-bars", type=int, default=0, help="If >0, exit after N bars if progress < --min-progress-r.")
    p.add_argument("--min-progress-r", type=float, default=0.3, help="Minimum favorable move in R for time-stop check.")
    p.add_argument("--break-even-r", type=float, default=0.0, help="If >0, move stop to entry once favorable move reaches this R.")
    p.add_argument("--trail-mode", choices=["none", "ema", "vwap"], default="none")
    p.add_argument("--trail-after-r", type=float, default=1.0, help="Activate trailing stop after this favorable R.")

    p.add_argument("--run-experiments", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    symbols, tv_exchange_map = _resolve_symbols_and_exchange_map(args)
    tv_username = args.tv_username or os.getenv("TVDATAFEED_USERNAME", "")
    tv_password = args.tv_password or os.getenv("TVDATAFEED_PASSWORD", "")
    alpaca_key = args.alpaca_key or os.getenv("APCA_API_KEY_ID", "")
    alpaca_secret = args.alpaca_secret or os.getenv("APCA_API_SECRET_KEY", "")

    if args.run_experiments:
        _run_experiments(args)
        return

    trades_df, summary, days_df = run_shared_cash_backtest(
        symbols=symbols,
        period=args.period,
        interval=args.interval,
        start_shares_each=args.start_shares_each,
        start_cash_total=args.start_cash_total,
        trade_fraction=args.trade_fraction,
        data_source=args.data_source,
        tv_n_bars=args.tv_n_bars,
        tv_username=tv_username,
        tv_password=tv_password,
        tv_exchange_map=tv_exchange_map,
        tv_default_exchange=args.tv_default_exchange,
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
        alpaca_feed=args.alpaca_feed,
        alpaca_base_url=args.alpaca_base_url,
        cache_db=args.cache_db,
        cache_disabled=args.no_cache,
        cache_refresh=args.cache_refresh,
        direction_mode=args.direction_mode,
        min_breakout_frac=args.min_breakout_frac,
        min_rvol=args.min_rvol,
        rvol_lookback_bars=args.rvol_lookback_bars,
        use_vwap_filter=args.use_vwap_filter,
        use_ema_slope_filter=args.use_ema_slope_filter,
        entry_cutoff_hhmm=args.entry_cutoff,
        exit_mode=args.exit_mode,
        stop_or_mult=args.stop_or_mult,
        target_or_mult=args.target_or_mult,
        time_stop_bars=args.time_stop_bars,
        min_progress_r=args.min_progress_r,
        break_even_r=args.break_even_r,
        trail_mode=args.trail_mode,
        trail_after_r=args.trail_after_r,
    )

    print("=== ORB_TEST Shared-Cash Summary ===")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"{k:>24}: {v:,.2f}")
        else:
            print(f"{k:>24}: {v}")

    if not trades_df.empty:
        by_sym = trades_df.groupby("symbol", as_index=False).agg(
            trades=("quantity", lambda s: int((s > 0).sum())),
            pnl=("pnl", "sum"),
        )
        print("\nPer-symbol executed trades and strategy PnL:")
        print(by_sym.to_string(index=False))

        by_exit = trades_df[trades_df["quantity"] > 0].groupby("exit_reason", as_index=False).agg(
            trades=("quantity", "count"),
            pnl=("pnl", "sum"),
        )
        print("\nExit reason breakdown:")
        print(by_exit.to_string(index=False))

    print("\nRecent 10 session-end rows:")
    print(days_df.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
