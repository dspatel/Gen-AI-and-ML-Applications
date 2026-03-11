from __future__ import annotations

import argparse
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from zoneinfo import ZoneInfo


SESSION_TZ = ZoneInfo("America/New_York")
DEFAULT_CACHE_DB = str(Path(__file__).with_name("data") / "options_cache.sqlite")


@dataclass
class PlannedTrade:
    symbol: str
    side: str
    signal_date: pd.Timestamp
    option_symbol: str
    expiry_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest a simple Alpaca options agent with cash sizing.")
    p.add_argument("--symbols", default="SPY,QQQ,NVDA")
    p.add_argument("--period-days", type=int, default=420)
    p.add_argument("--start-cash", type=float, default=100000.0)
    p.add_argument("--trade-fraction", type=float, default=0.10)
    p.add_argument("--max-open-positions", type=int, default=6)
    p.add_argument("--side-mode", choices=["both", "long", "short"], default="both")

    p.add_argument("--ema-length", type=int, default=20)
    p.add_argument("--breakout-lookback", type=int, default=20)
    p.add_argument("--moneyness-pct", type=float, default=0.01)

    p.add_argument("--min-dte", type=int, default=20)
    p.add_argument("--max-dte", type=int, default=45)
    p.add_argument("--hold-days", type=int, default=5)
    p.add_argument("--stop-loss-pct", type=float, default=0.35)
    p.add_argument("--take-profit-pct", type=float, default=0.60)
    p.add_argument("--min-option-price", type=float, default=0.10)

    p.add_argument("--alpaca-key", default="")
    p.add_argument("--alpaca-secret", default="")
    p.add_argument("--data-base-url", default="https://data.alpaca.markets")
    p.add_argument("--paper-base-url", default="https://paper-api.alpaca.markets")
    p.add_argument("--stock-feed", choices=["iex", "sip", "otc"], default="iex")

    p.add_argument("--cache-db", default=DEFAULT_CACHE_DB)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--cache-refresh", action="store_true")
    return p.parse_args()


def _credentials(args: argparse.Namespace) -> tuple[str, str]:
    key = args.alpaca_key.strip() or os.getenv("APCA_API_KEY_ID", "").strip()
    secret = args.alpaca_secret.strip() or os.getenv("APCA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise RuntimeError("Missing Alpaca credentials. Set APCA_API_KEY_ID/APCA_API_SECRET_KEY or pass args.")
    return key, secret


def _headers(key: str, secret: str) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def _request_json(
    *,
    url: str,
    headers: dict[str, str],
    params: dict,
    timeout: int = 30,
    retries: int = 4,
) -> dict:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(min(0.5 * attempt, 3.0))
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:240]}")
            return resp.json()
        except Exception as exc:
            last_err = exc
            time.sleep(min(0.5 * attempt, 2.0))
    raise RuntimeError(f"Request failed after retries: {url} params={params} err={last_err}")


def _ensure_cache_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_bars (
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
        CREATE TABLE IF NOT EXISTS option_bars (
            source_key TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            bar_interval TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY (source_key, option_symbol, bar_interval, ts_utc)
        )
        """
    )


def _cache_load(
    cache_db: str,
    table: str,
    key_col: str,
    key_val: str,
    interval: str,
    source_key: str,
) -> pd.DataFrame:
    p = Path(cache_db)
    if not p.exists():
        return pd.DataFrame()
    with sqlite3.connect(str(p)) as conn:
        _ensure_cache_schema(conn)
        query = (
            f"SELECT ts_utc, open, high, low, close, volume "
            f"FROM {table} "
            f"WHERE source_key=? AND {key_col}=? AND bar_interval=? "
            f"ORDER BY ts_utc ASC"
        )
        rows = conn.execute(query, (source_key, key_val, interval)).fetchall()
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows, columns=["ts_utc", "open", "high", "low", "close", "volume"])
    out["time_local"] = pd.to_datetime(out["ts_utc"], utc=True).dt.tz_convert(SESSION_TZ)
    return out[["time_local", "open", "high", "low", "close", "volume"]]


def _cache_store(
    cache_db: str,
    table: str,
    key_col: str,
    key_val: str,
    interval: str,
    source_key: str,
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
            [key_val] * len(bars),
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
            f"""
            INSERT INTO {table} (
                source_key, {key_col}, bar_interval, ts_utc,
                open, high, low, close, volume, fetched_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, {key_col}, bar_interval, ts_utc)
            DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                volume=excluded.volume, fetched_at_utc=excluded.fetched_at_utc
            """,
            rows,
        )
        conn.commit()


def _fetch_stock_daily_bars(
    *,
    symbol: str,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    key: str,
    secret: str,
    data_base_url: str,
    stock_feed: str,
    cache_db: str,
    no_cache: bool,
    cache_refresh: bool,
) -> pd.DataFrame:
    source_key = f"alpaca_stocks:{stock_feed}"
    if not no_cache and not cache_refresh:
        cached = _cache_load(cache_db, "stock_bars", "symbol", symbol, "1Day", source_key)
        if not cached.empty:
            c_start = cached["time_local"].dt.normalize().min()
            c_end = cached["time_local"].dt.normalize().max()
            if c_start <= start_utc.tz_convert(SESSION_TZ).normalize() and c_end >= end_utc.tz_convert(SESSION_TZ).normalize():
                return cached[(cached["time_local"] >= start_utc.tz_convert(SESSION_TZ)) & (cached["time_local"] <= end_utc.tz_convert(SESSION_TZ))]

    url = f"{data_base_url.rstrip('/')}/v2/stocks/bars"
    headers = _headers(key, secret)
    page_token: Optional[str] = None
    rows: list[dict] = []
    while True:
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start_utc.isoformat(),
            "end": end_utc.isoformat(),
            "adjustment": "split",
            "feed": stock_feed,
            "sort": "asc",
            "limit": 10000,
        }
        if page_token:
            params["page_token"] = page_token
        payload = _request_json(url=url, headers=headers, params=params)
        rows.extend(payload.get("bars", {}).get(symbol, []) or [])
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "time_local": pd.to_datetime(df["t"], utc=True).dt.tz_convert(SESSION_TZ),
            "open": df["o"].astype(float),
            "high": df["h"].astype(float),
            "low": df["l"].astype(float),
            "close": df["c"].astype(float),
            "volume": df["v"].astype(float),
        }
    ).sort_values("time_local")
    out = out.drop_duplicates(subset=["time_local"]).reset_index(drop=True)
    if not no_cache:
        _cache_store(cache_db, "stock_bars", "symbol", symbol, "1Day", source_key, out)
    return out


def _fetch_option_contracts(
    *,
    underlying: str,
    opt_type: str,
    signal_date: pd.Timestamp,
    min_dte: int,
    max_dte: int,
    key: str,
    secret: str,
    paper_base_url: str,
) -> pd.DataFrame:
    headers = _headers(key, secret)
    start_exp = (signal_date + pd.Timedelta(days=min_dte)).date().isoformat()
    end_exp = (signal_date + pd.Timedelta(days=max_dte)).date().isoformat()
    url = f"{paper_base_url.rstrip('/')}/v2/options/contracts"

    all_rows: list[dict] = []
    for status in ["active", "inactive"]:
        page_token: Optional[str] = None
        while True:
            params = {
                "underlying_symbols": underlying,
                "status": status,
                "type": opt_type,
                "expiration_date_gte": start_exp,
                "expiration_date_lte": end_exp,
                "limit": 1000,
            }
            if page_token:
                params["page_token"] = page_token
            payload = _request_json(url=url, headers=headers, params=params)
            rows = payload.get("option_contracts", []) or []
            all_rows.extend(rows)
            page_token = payload.get("next_page_token")
            if not page_token:
                break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    keep_cols = ["symbol", "expiration_date", "strike_price", "type", "status", "open_interest", "tradable"]
    for c in keep_cols:
        if c not in df.columns:
            df[c] = np.nan
    out = df[keep_cols].copy()
    out["expiration_date"] = pd.to_datetime(out["expiration_date"]).dt.normalize()
    out["strike_price"] = pd.to_numeric(out["strike_price"], errors="coerce")
    out["open_interest"] = pd.to_numeric(out["open_interest"], errors="coerce").fillna(0.0)
    out = out.dropna(subset=["symbol", "expiration_date", "strike_price"]).drop_duplicates(subset=["symbol"]).reset_index(drop=True)
    return out


def _fetch_option_daily_bars(
    *,
    option_symbol: str,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    key: str,
    secret: str,
    data_base_url: str,
    cache_db: str,
    no_cache: bool,
    cache_refresh: bool,
) -> pd.DataFrame:
    source_key = "alpaca_options"
    if not no_cache and not cache_refresh:
        cached = _cache_load(cache_db, "option_bars", "option_symbol", option_symbol, "1Day", source_key)
        if not cached.empty:
            c_start = cached["time_local"].dt.normalize().min()
            c_end = cached["time_local"].dt.normalize().max()
            if c_start <= start_utc.tz_convert(SESSION_TZ).normalize() and c_end >= end_utc.tz_convert(SESSION_TZ).normalize():
                return cached[(cached["time_local"] >= start_utc.tz_convert(SESSION_TZ)) & (cached["time_local"] <= end_utc.tz_convert(SESSION_TZ))]

    url = f"{data_base_url.rstrip('/')}/v1beta1/options/bars"
    headers = _headers(key, secret)
    page_token: Optional[str] = None
    rows: list[dict] = []
    while True:
        params = {
            "symbols": option_symbol,
            "timeframe": "1Day",
            "start": start_utc.isoformat(),
            "end": end_utc.isoformat(),
            "sort": "asc",
            "limit": 10000,
        }
        if page_token:
            params["page_token"] = page_token
        try:
            payload = _request_json(url=url, headers=headers, params=params)
        except RuntimeError as exc:
            if "OPRA agreement is not signed" in str(exc):
                return pd.DataFrame()
            raise
        rows.extend(payload.get("bars", {}).get(option_symbol, []) or [])
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "time_local": pd.to_datetime(df["t"], utc=True).dt.tz_convert(SESSION_TZ),
            "open": df["o"].astype(float),
            "high": df["h"].astype(float),
            "low": df["l"].astype(float),
            "close": df["c"].astype(float),
            "volume": df["v"].astype(float),
        }
    ).sort_values("time_local")
    out = out.drop_duplicates(subset=["time_local"]).reset_index(drop=True)
    if not no_cache:
        _cache_store(cache_db, "option_bars", "option_symbol", option_symbol, "1Day", source_key, out)
    return out


def _build_signals(df: pd.DataFrame, ema_len: int, breakout_lookback: int) -> pd.DataFrame:
    out = df.copy().sort_values("time_local").reset_index(drop=True)
    out["session_date"] = out["time_local"].dt.tz_convert(SESSION_TZ).dt.tz_localize(None).dt.normalize()
    out["ema"] = out["close"].ewm(span=ema_len, adjust=False).mean()
    out["hh"] = out["high"].rolling(breakout_lookback).max().shift(1)
    out["ll"] = out["low"].rolling(breakout_lookback).min().shift(1)
    out["signal"] = ""
    long_mask = (out["close"] > out["ema"]) & (out["close"] > out["hh"])
    short_mask = (out["close"] < out["ema"]) & (out["close"] < out["ll"])
    out.loc[long_mask.fillna(False), "signal"] = "long"
    out.loc[short_mask.fillna(False), "signal"] = "short"
    return out


def _pick_contract(
    *,
    underlying: str,
    side: str,
    signal_date: pd.Timestamp,
    underlying_price: float,
    moneyness_pct: float,
    min_dte: int,
    max_dte: int,
    key: str,
    secret: str,
    paper_base_url: str,
) -> Optional[pd.Series]:
    opt_type = "call" if side == "long" else "put"
    contracts = _fetch_option_contracts(
        underlying=underlying,
        opt_type=opt_type,
        signal_date=signal_date,
        min_dte=min_dte,
        max_dte=max_dte,
        key=key,
        secret=secret,
        paper_base_url=paper_base_url,
    )
    if contracts.empty:
        return None

    target_strike = underlying_price * (1.0 + moneyness_pct) if side == "long" else underlying_price * (1.0 - moneyness_pct)
    contracts["strike_dist"] = (contracts["strike_price"] - target_strike).abs()
    contracts = contracts.sort_values(["expiration_date", "strike_dist", "open_interest"], ascending=[True, True, False]).reset_index(drop=True)
    return contracts.iloc[0]


def _plan_trade(
    *,
    symbol: str,
    side: str,
    signal_date: pd.Timestamp,
    underlying_close: float,
    args: argparse.Namespace,
    key: str,
    secret: str,
) -> Optional[PlannedTrade]:
    sdate = pd.Timestamp(signal_date)
    if sdate.tzinfo is not None:
        sdate = sdate.tz_convert(SESSION_TZ).tz_localize(None)
    sdate = sdate.normalize()

    contract = _pick_contract(
        underlying=symbol,
        side=side,
        signal_date=sdate,
        underlying_price=float(underlying_close),
        moneyness_pct=float(args.moneyness_pct),
        min_dte=int(args.min_dte),
        max_dte=int(args.max_dte),
        key=key,
        secret=secret,
        paper_base_url=args.paper_base_url,
    )
    if contract is None:
        return None

    option_symbol = str(contract["symbol"])
    expiry = pd.Timestamp(contract["expiration_date"]).normalize()
    end_date = min(expiry, sdate + pd.Timedelta(days=int(args.hold_days) + 12))
    bars = _fetch_option_daily_bars(
        option_symbol=option_symbol,
        start_utc=(sdate - pd.Timedelta(days=2)).tz_localize(SESSION_TZ).tz_convert("UTC"),
        end_utc=(end_date + pd.Timedelta(days=1)).tz_localize(SESSION_TZ).tz_convert("UTC"),
        key=key,
        secret=secret,
        data_base_url=args.data_base_url,
        cache_db=args.cache_db,
        no_cache=bool(args.no_cache),
        cache_refresh=bool(args.cache_refresh),
    )
    if bars.empty:
        return None

    bars["session_date"] = bars["time_local"].dt.tz_convert(SESSION_TZ).dt.tz_localize(None).dt.normalize()
    bars = bars.sort_values("session_date").drop_duplicates(subset=["session_date"]).reset_index(drop=True)
    future = bars[bars["session_date"] >= sdate].copy()
    if future.empty:
        return None

    entry_row = future.iloc[0]
    entry_date = pd.Timestamp(entry_row["session_date"]).normalize()
    entry_price = float(entry_row["close"])
    if entry_price < float(args.min_option_price):
        return None

    scan = future[future["session_date"] > entry_date].copy()
    if scan.empty:
        return None

    stop_r = -abs(float(args.stop_loss_pct))
    tgt_r = abs(float(args.take_profit_pct))
    hold_days = int(args.hold_days)
    forced_exit_date = min(expiry, entry_date + pd.Timedelta(days=hold_days))

    exit_date = pd.Timestamp(scan.iloc[-1]["session_date"]).normalize()
    exit_price = float(scan.iloc[-1]["close"])
    exit_reason = "last_available"
    for r in scan.itertuples(index=False):
        d = pd.Timestamp(r.session_date).normalize()
        ret = float(r.close) / max(entry_price, 1e-12) - 1.0
        if ret <= stop_r:
            exit_date = d
            exit_price = float(r.close)
            exit_reason = "stop_loss"
            break
        if ret >= tgt_r:
            exit_date = d
            exit_price = float(r.close)
            exit_reason = "take_profit"
            break
        if d >= forced_exit_date:
            exit_date = d
            exit_price = float(r.close)
            exit_reason = "time_exit"
            break

    return PlannedTrade(
        symbol=symbol,
        side=side,
        signal_date=sdate,
        option_symbol=option_symbol,
        expiry_date=expiry,
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        exit_reason=exit_reason,
    )


def _cagr(start_value: float, end_value: float, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float:
    days = max((end_date - start_date).days, 1)
    yrs = days / 365.25
    if yrs <= 0 or start_value <= 0:
        return 0.0
    return (end_value / start_value) ** (1.0 / yrs) - 1.0


def main() -> None:
    args = _parse_args()
    key, secret = _credentials(args)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise ValueError("No symbols provided.")

    end_utc = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    start_utc = end_utc - pd.Timedelta(days=max(int(args.period_days) * 2, 120))

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for s in symbols:
        df = _fetch_stock_daily_bars(
            symbol=s,
            start_utc=start_utc,
            end_utc=end_utc,
            key=key,
            secret=secret,
            data_base_url=args.data_base_url,
            stock_feed=args.stock_feed,
            cache_db=args.cache_db,
            no_cache=bool(args.no_cache),
            cache_refresh=bool(args.cache_refresh),
        )
        if df.empty:
            continue
        sig = _build_signals(df=df, ema_len=int(args.ema_length), breakout_lookback=int(args.breakout_lookback))
        bars_by_symbol[s] = sig

    if not bars_by_symbol:
        raise RuntimeError("No underlying bars were loaded.")

    common_dates = sorted(set.intersection(*[set(df["session_date"].unique().tolist()) for df in bars_by_symbol.values()]))
    if not common_dates:
        raise RuntimeError("No common session dates across selected symbols.")
    if len(common_dates) > int(args.period_days):
        common_dates = common_dates[-int(args.period_days) :]

    cash = float(args.start_cash)
    start_cash = cash
    open_positions: list[dict] = []
    trade_rows: list[dict] = []

    for dt in common_dates:
        dt = pd.Timestamp(dt).normalize()
        still_open: list[dict] = []
        for p in open_positions:
            if pd.Timestamp(p["exit_date"]).normalize() <= dt:
                proceeds = float(p["contracts"]) * float(p["exit_price"]) * 100.0
                pnl = proceeds - float(p["cost"])
                cash += proceeds
                trade_rows.append(
                    {
                        "symbol": p["symbol"],
                        "side": p["side"],
                        "signal_date": p["signal_date"],
                        "option_symbol": p["option_symbol"],
                        "entry_date": p["entry_date"],
                        "entry_price": p["entry_price"],
                        "exit_date": p["exit_date"],
                        "exit_price": p["exit_price"],
                        "exit_reason": p["exit_reason"],
                        "contracts": p["contracts"],
                        "cost": p["cost"],
                        "proceeds": proceeds,
                        "pnl": pnl,
                        "cash_after": cash,
                    }
                )
            else:
                still_open.append(p)
        open_positions = still_open

        if len(open_positions) >= int(args.max_open_positions):
            continue

        open_symbols = {str(p["symbol"]) for p in open_positions}
        for s in symbols:
            if len(open_positions) >= int(args.max_open_positions):
                break
            if s in open_symbols:
                continue
            df = bars_by_symbol.get(s)
            if df is None:
                continue
            row = df[df["session_date"] == dt]
            if row.empty:
                continue
            sig = str(row.iloc[0]["signal"])
            if sig not in {"long", "short"}:
                continue
            if args.side_mode == "long" and sig != "long":
                continue
            if args.side_mode == "short" and sig != "short":
                continue

            planned = _plan_trade(
                symbol=s,
                side=sig,
                signal_date=dt,
                underlying_close=float(row.iloc[0]["close"]),
                args=args,
                key=key,
                secret=secret,
            )
            if planned is None:
                continue

            budget = cash * float(args.trade_fraction)
            contracts = int(np.floor(budget / max(planned.entry_price * 100.0, 1e-9)))
            if contracts < 1:
                continue
            cost = contracts * planned.entry_price * 100.0
            if cost > cash:
                continue

            cash -= cost
            open_positions.append(
                {
                    "symbol": s,
                    "side": sig,
                    "signal_date": planned.signal_date.date().isoformat(),
                    "option_symbol": planned.option_symbol,
                    "entry_date": planned.entry_date.date().isoformat(),
                    "entry_price": planned.entry_price,
                    "exit_date": planned.exit_date.date().isoformat(),
                    "exit_price": planned.exit_price,
                    "exit_reason": planned.exit_reason,
                    "contracts": contracts,
                    "cost": cost,
                }
            )
            open_symbols.add(s)

    # Force close leftovers at planned exit values.
    for p in open_positions:
        proceeds = float(p["contracts"]) * float(p["exit_price"]) * 100.0
        pnl = proceeds - float(p["cost"])
        cash += proceeds
        trade_rows.append(
            {
                "symbol": p["symbol"],
                "side": p["side"],
                "signal_date": p["signal_date"],
                "option_symbol": p["option_symbol"],
                "entry_date": p["entry_date"],
                "entry_price": p["entry_price"],
                "exit_date": p["exit_date"],
                "exit_price": p["exit_price"],
                "exit_reason": "forced_" + str(p["exit_reason"]),
                "contracts": p["contracts"],
                "cost": p["cost"],
                "proceeds": proceeds,
                "pnl": pnl,
                "cash_after": cash,
            }
        )

    trades = pd.DataFrame(trade_rows)
    start_date = pd.Timestamp(common_dates[0]).normalize()
    end_date = pd.Timestamp(common_dates[-1]).normalize()
    total_return = cash / max(start_cash, 1e-9) - 1.0
    cagr = _cagr(start_cash, cash, start_date, end_date)

    print("=== OPTIONS_AGENT Backtest Summary ===")
    print(f"symbols: {','.join(symbols)}")
    print(f"period_days: {args.period_days}")
    print(f"side_mode: {args.side_mode}")
    print(f"start_date: {start_date.date().isoformat()}")
    print(f"end_date: {end_date.date().isoformat()}")
    print(f"start_cash: {start_cash:,.2f}")
    print(f"final_cash: {cash:,.2f}")
    print(f"cash_change: {cash - start_cash:,.2f}")
    print(f"total_return_pct: {100.0 * total_return:.2f}")
    print(f"cash_cagr_pct: {100.0 * cagr:.2f}")
    print(f"trades_closed: {len(trades)}")
    if not trades.empty:
        wins = int((trades["pnl"] > 0).sum())
        print(f"win_rate_pct: {100.0 * wins / len(trades):.2f}")
        print("\nPnL by symbol:")
        by_sym = trades.groupby("symbol", as_index=False)["pnl"].sum().sort_values("pnl", ascending=False)
        print(by_sym.to_string(index=False))
        print("\nExit reason breakdown:")
        by_exit = trades.groupby("exit_reason", as_index=False).agg(trades=("exit_reason", "count"), pnl=("pnl", "sum"))
        by_exit = by_exit.sort_values("trades", ascending=False)
        print(by_exit.to_string(index=False))
        print("\nRecent 10 trades:")
        print(trades.tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
