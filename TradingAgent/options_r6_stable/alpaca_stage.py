from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .config_loader import AppConfig
from .contract_selector import required_right, select_contract
from .greeks import implied_volatility, option_delta
from .historical_provider import HistoricalDataProvider, build_historical_provider
from .models import OptionContractSnapshot, UnderlyingSignal


UTC = timezone.utc
HISTORICAL_OPTIONS_START = "2024-02-01"
DEFAULT_STRIKE_BAND_PCT = 0.15
DEFAULT_MAX_ENTRY_DELAY_MINUTES = 5
DEFAULT_OPTION_BAR_TIMEFRAME = "1Min"
DEFAULT_UNDERLYING_PATH_TIMEFRAME = "1Min"
DEFAULT_EXECUTION_FRICTION_PCT = 0.0025
DEFAULT_EXECUTION_FRICTION_MIN = 0.01


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _stable_id(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1()
    h.update(prefix.encode("utf-8"))
    for part in parts:
        h.update(b"\x1f")
        h.update(_json_dumps(part).encode("utf-8"))
    return f"{prefix}_{h.hexdigest()[:20]}"


def _parse_ts(value: str, timezone_name: str) -> datetime:
    normalized = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return dt.astimezone(ZoneInfo(timezone_name))


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _session_dt(session_date: str, hhmm: str, timezone_name: str) -> datetime:
    hour, minute = [int(part) for part in str(hhmm).split(":")]
    return datetime.fromisoformat(session_date).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
        tzinfo=ZoneInfo(timezone_name),
    )


def _timeframe_to_minutes(timeframe: str) -> int:
    raw = str(timeframe or "").strip().lower()
    if raw.endswith("min"):
        return max(1, int(raw[:-3]))
    if raw.endswith("m"):
        return max(1, int(raw[:-1]))
    raise ValueError(f"Unsupported timeframe: {timeframe!r}")


def _upsert(conn: sqlite3.Connection, table: str, pk_col: str, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_cols = [c for c in columns if c != pk_col]
    update_sql = ", ".join([f"{c}=excluded.{c}" for c in update_cols])
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk_col}) DO UPDATE SET {update_sql}"
    )
    conn.execute(sql, tuple(row[c] for c in columns))


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, tuple(row[c] for c in columns))


def _upsert_underlying_bar(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    ts: str,
    timeframe_min: int,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
    volume: float,
    source_provider: str,
    session_date: str,
    provider_ts: str,
    ingested_at: str,
    bar_status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO underlying_bars_intraday (
            symbol, ts, timeframe_min, open, high, low, close, volume, source_provider,
            session_date, provider_ts, ingested_at, bar_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, ts, timeframe_min) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            source_provider=excluded.source_provider,
            session_date=excluded.session_date,
            provider_ts=excluded.provider_ts,
            ingested_at=excluded.ingested_at,
            bar_status=excluded.bar_status
        """,
        (
            symbol,
            ts,
            timeframe_min,
            open_px,
            high_px,
            low_px,
            close_px,
            volume,
            source_provider,
            session_date,
            provider_ts,
            ingested_at,
            bar_status,
        ),
    )


def _regular_session_bounds(session_date: str, cfg: AppConfig) -> tuple[datetime, datetime]:
    return (
        _session_dt(session_date, cfg.session.start, cfg.timezone),
        _session_dt(session_date, cfg.session.end, cfg.timezone),
    )


def _filter_stock_bars(rows: Iterable[dict[str, Any]], cfg: AppConfig, timeframe_min: int | None = None) -> list[dict[str, Any]]:
    timeframe_min = int(timeframe_min or _timeframe_to_minutes(cfg.underlying_signal.interval))
    filtered: list[dict[str, Any]] = []
    for row in rows:
        start_utc = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).astimezone(UTC)
        start_ct = start_utc.astimezone(ZoneInfo(cfg.timezone))
        close_ct = start_ct + timedelta(minutes=timeframe_min)
        session_open, session_close = _regular_session_bounds(start_ct.date().isoformat(), cfg)
        if start_ct < session_open or close_ct > session_close:
            continue
        filtered.append(
            {
                "start_utc": start_utc,
                "start_ct": start_ct,
                "close_ct": close_ct,
                "o": float(row["o"]),
                "h": float(row["h"]),
                "l": float(row["l"]),
                "c": float(row["c"]),
                "v": float(row.get("v") or 0.0),
                "raw": dict(row),
            }
        )
    filtered.sort(key=lambda item: item["start_ct"])
    alpha = 2.0 / 21.0
    prev_ema: float | None = None
    ema_values: list[float] = []
    for item in filtered:
        close_px = float(item["c"])
        prev_ema = close_px if prev_ema is None else (alpha * close_px) + ((1.0 - alpha) * prev_ema)
        item["ema20"] = prev_ema
        ema_values.append(prev_ema)
    for idx, item in enumerate(filtered):
        item["ema20_slope"] = None if idx < 3 else float(item["ema20"]) - float(ema_values[idx - 3])
    return filtered


def _store_underlying_bars(
    conn: sqlite3.Connection,
    symbol: str,
    bars: list[dict[str, Any]],
    cfg: AppConfig,
    *,
    timeframe_min: int | None = None,
) -> None:
    timeframe_min = int(timeframe_min or _timeframe_to_minutes(cfg.underlying_signal.interval))
    ingested_at = datetime.now(tz=ZoneInfo(cfg.timezone)).isoformat()
    for bar in bars:
        _upsert_underlying_bar(
            conn,
            symbol=symbol,
            ts=bar["start_ct"].isoformat(),
            timeframe_min=timeframe_min,
            open_px=float(bar["o"]),
            high_px=float(bar["h"]),
            low_px=float(bar["l"]),
            close_px=float(bar["c"]),
            volume=float(bar["v"]),
            source_provider="alpaca",
            session_date=bar["start_ct"].date().isoformat(),
            provider_ts=bar["start_utc"].isoformat(),
            ingested_at=ingested_at,
            bar_status="historical",
        )


def _find_event_bar(bars: list[dict[str, Any]], event_dt_ct: datetime, cfg: AppConfig) -> dict[str, Any]:
    timeframe_min = _timeframe_to_minutes(cfg.underlying_signal.interval)
    candidates = [bar for bar in bars if bar["close_ct"] <= event_dt_ct]
    if not candidates:
        raise ValueError(f"No completed {cfg.underlying_signal.interval} underlying bar found before {event_dt_ct.isoformat()}")
    bar = candidates[-1]
    if (event_dt_ct - bar["close_ct"]) > timedelta(minutes=timeframe_min):
        raise ValueError(f"Nearest completed underlying bar is stale for event {event_dt_ct.isoformat()}")
    return bar


def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for idx in range(0, len(values), max(1, int(size))):
        yield values[idx : idx + max(1, int(size))]


def _entry_friction(raw_price: float) -> float:
    return max(DEFAULT_EXECUTION_FRICTION_MIN, float(raw_price) * DEFAULT_EXECUTION_FRICTION_PCT)


def _years_to_expiry(expiration_date: str, asof_dt_ct: datetime, timezone_name: str) -> float:
    expiry_dt = datetime.fromisoformat(str(expiration_date)).replace(
        hour=15,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=ZoneInfo(timezone_name),
    )
    seconds = max((expiry_dt - asof_dt_ct).total_seconds(), 60.0)
    return seconds / (365.0 * 24.0 * 60.0 * 60.0)


def _in_window(session_date: str, start: str | None, end: str | None) -> bool:
    if start and session_date < start:
        return False
    if end and session_date > end:
        return False
    return True


def _split_bucket(session_date: str, cfg: AppConfig) -> str:
    if _in_window(session_date, cfg.research.blind.start, cfg.research.blind.end):
        return "blind"
    if _in_window(session_date, cfg.research.validation.start, cfg.research.validation.end):
        return "validation"
    if _in_window(session_date, cfg.research.train.start, cfg.research.train.end):
        return "train"
    return "unspecified"


def _contract_statuses_to_query(
    *,
    expiration_date_gte: str,
    expiration_date_lte: str,
    timezone_name: str,
) -> list[str | None]:
    today_ct = datetime.now(tz=ZoneInfo(timezone_name)).date().isoformat()
    if expiration_date_lte < today_ct:
        return ["inactive"]
    if expiration_date_gte > today_ct:
        return ["active"]
    return ["active", "inactive"]


def _dedupe_contract_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str((row or {}).get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(row)
    return out


def _annotate_option_bars(rows: Iterable[dict[str, Any]], cfg: AppConfig) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for row in rows:
        start_utc = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).astimezone(UTC)
        start_ct = start_utc.astimezone(ZoneInfo(cfg.timezone))
        close_ct = start_ct + timedelta(minutes=1)
        session_open, session_close = _regular_session_bounds(start_ct.date().isoformat(), cfg)
        if start_ct < session_open or close_ct > session_close:
            continue
        annotated.append(
            {
                "start_utc": start_utc,
                "start_ct": start_ct,
                "close_ct": close_ct,
                "o": float(row["o"]),
                "h": float(row["h"]),
                "l": float(row["l"]),
                "c": float(row["c"]),
                "v": float(row.get("v") or 0.0),
                "raw": dict(row),
            }
        )
    annotated.sort(key=lambda item: item["start_ct"])
    return annotated


def _pick_entry_bar(option_bars: list[dict[str, Any]], event_dt_ct: datetime) -> dict[str, Any] | None:
    max_dt = event_dt_ct + timedelta(minutes=DEFAULT_MAX_ENTRY_DELAY_MINUTES)
    for bar in option_bars:
        if bar["start_ct"] >= event_dt_ct and bar["start_ct"] <= max_dt:
            return bar
    return None


def _pick_exit_bar(option_bars: list[dict[str, Any]], exit_dt_ct: datetime) -> dict[str, Any] | None:
    for bar in option_bars:
        if bar["start_ct"] >= exit_dt_ct:
            return bar
    prior = [bar for bar in option_bars if bar["start_ct"] <= exit_dt_ct]
    return None if not prior else prior[-1]


def _find_exit_decision_dt(
    stock_bars: list[dict[str, Any]],
    signal: UnderlyingSignal,
    force_exit_dt_ct: datetime,
) -> tuple[datetime, str]:
    event_dt_ct = _parse_ts(signal.event_ts, force_exit_dt_ct.tzinfo.key if hasattr(force_exit_dt_ct.tzinfo, "key") else "America/Chicago")
    if signal.underlying_stop_price is None:
        return force_exit_dt_ct, "time_exit"
    for bar in stock_bars:
        if bar["close_ct"] <= event_dt_ct:
            continue
        if bar["close_ct"] > force_exit_dt_ct:
            break
        if signal.direction == "BULLISH" and float(bar["l"]) <= float(signal.underlying_stop_price):
            return bar["close_ct"], "underlying_invalidation_exit"
        if signal.direction == "BEARISH" and float(bar["h"]) >= float(signal.underlying_stop_price):
            return bar["close_ct"], "underlying_invalidation_exit"
    return force_exit_dt_ct, "time_exit"


def _build_contract_snapshot(
    contract_row: dict[str, Any],
    signal: UnderlyingSignal,
    entry_bar: dict[str, Any],
    cumulative_volume: int,
    cfg: AppConfig,
) -> OptionContractSnapshot:
    expiration_date = str(contract_row["expiration_date"])
    premium_proxy = float(entry_bar["o"])
    years = _years_to_expiry(expiration_date, entry_bar["start_ct"], cfg.timezone)
    risk_free_rate = 0.04
    iv = implied_volatility(
        right=str(contract_row["type"]).lower(),
        premium=premium_proxy,
        spot=float(signal.underlying_price or 0.0),
        strike=float(contract_row["strike_price"]),
        years_to_expiry=years,
        risk_free_rate=risk_free_rate,
    )
    delta = None if iv is None else option_delta(
        right=str(contract_row["type"]).lower(),
        spot=float(signal.underlying_price or 0.0),
        strike=float(contract_row["strike_price"]),
        years_to_expiry=years,
        risk_free_rate=risk_free_rate,
        sigma=iv,
    )
    open_interest = contract_row.get("open_interest")
    return OptionContractSnapshot(
        option_symbol=str(contract_row["symbol"]).strip().upper(),
        underlying_symbol=signal.symbol,
        right=str(contract_row["type"]).strip().lower(),
        expiration_date=expiration_date,
        strike=float(contract_row["strike_price"]),
        dte=max(0, (datetime.fromisoformat(expiration_date).date() - entry_bar["start_ct"].date()).days),
        bid=premium_proxy,
        ask=premium_proxy,
        delta=delta,
        open_interest=(None if open_interest in (None, "", "null") else int(float(open_interest))),
        volume=max(0, int(cumulative_volume)),
        last=float(entry_bar["c"]),
        iv=iv,
        gamma=None,
        theta=None,
        vega=None,
    )


def _build_chain_row(
    event_id: str,
    signal: UnderlyingSignal,
    snapshot: OptionContractSnapshot,
    entry_bar: dict[str, Any],
) -> dict[str, Any]:
    notes = {
        "source": "alpaca_historical_option_bar_proxy",
        "proxy_method": "entry_minute_open_used_for_bid_ask_mid",
        "open_interest_source": "alpaca_contract_metadata_latest_available",
        "greeks_source": "black_scholes_proxy_from_bar_open",
    }
    return {
        "event_id": event_id,
        "symbol": signal.symbol,
        "asof_ts": entry_bar["start_ct"].isoformat(),
        "option_symbol": snapshot.option_symbol,
        "right_side": snapshot.right,
        "expiration_date": snapshot.expiration_date,
        "strike": snapshot.strike,
        "dte": snapshot.dte,
        "bid": snapshot.bid,
        "ask": snapshot.ask,
        "last": snapshot.last,
        "delta": snapshot.delta,
        "gamma": snapshot.gamma,
        "theta": snapshot.theta,
        "vega": snapshot.vega,
        "iv": snapshot.iv,
        "open_interest": snapshot.open_interest,
        "volume": snapshot.volume,
        "source_tag": "alpaca_historical_stage_v1",
        "notes_json": _json_dumps(notes),
    }


def _build_option_path_rows(
    *,
    event_id: str,
    option_symbol: str,
    option_bars: list[dict[str, Any]],
    event_dt_ct: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bar in option_bars:
        if bar["start_ct"] < event_dt_ct:
            continue
        rows.append(
            {
                "event_id": event_id,
                "option_symbol": option_symbol,
                "ts": bar["start_ct"].isoformat(),
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": float(bar["v"]),
                "source_tag": "alpaca_historical_stage_v1",
                "notes_json": _json_dumps(
                    {
                        "source": "alpaca_option_1m_bar",
                        "timeframe": DEFAULT_OPTION_BAR_TIMEFRAME,
                    }
                ),
            }
        )
    return rows


def _build_underlying_path_rows(
    *,
    event_id: str,
    symbol: str,
    underlying_bars: list[dict[str, Any]],
    event_dt_ct: datetime,
    timeframe_min: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bar in underlying_bars:
        if bar["start_ct"] < event_dt_ct:
            continue
        rows.append(
            {
                "event_id": event_id,
                "symbol": symbol,
                "ts": bar["start_ct"].isoformat(),
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": float(bar["v"]),
                "timeframe_min": int(timeframe_min),
                "source_tag": "alpaca_historical_stage_v1",
                "notes_json": _json_dumps(
                    {
                        "source": "alpaca_stock_bar",
                        "timeframe_min": int(timeframe_min),
                    }
                ),
            }
        )
    return rows


def _build_outcome_row(
    *,
    event_id: str,
    signal: UnderlyingSignal,
    snapshot: OptionContractSnapshot,
    option_bars: list[dict[str, Any]],
    stock_bars: list[dict[str, Any]],
    force_exit_dt_ct: datetime,
    cfg: AppConfig,
) -> dict[str, Any] | None:
    entry_bar = _pick_entry_bar(option_bars, _parse_ts(signal.event_ts, cfg.timezone))
    if entry_bar is None:
        return None
    exit_decision_dt_ct, exit_reason = _find_exit_decision_dt(stock_bars, signal, force_exit_dt_ct)
    exit_bar = _pick_exit_bar(option_bars, exit_decision_dt_ct)
    if exit_bar is None:
        return None
    entry_raw = float(entry_bar["o"])
    exit_raw = float(exit_bar["o"])
    entry_friction = _entry_friction(entry_raw)
    exit_friction = _entry_friction(exit_raw)
    entry_fill = entry_raw + entry_friction
    exit_fill = max(0.01, exit_raw - exit_friction)
    option_window = [bar for bar in option_bars if bar["start_ct"] >= entry_bar["start_ct"] and bar["start_ct"] <= exit_bar["start_ct"]]
    if not option_window:
        option_window = [entry_bar, exit_bar]
    stock_window = [bar for bar in stock_bars if bar["close_ct"] > _parse_ts(signal.event_ts, cfg.timezone) and bar["close_ct"] <= exit_decision_dt_ct]
    if not stock_window:
        stock_window = [next((bar for bar in stock_bars if bar["close_ct"] >= _parse_ts(signal.event_ts, cfg.timezone)), stock_bars[-1])]
    underlying_exit_px = float(stock_window[-1]["c"])
    peak_contract_mid = max(float(bar["h"]) for bar in option_window)
    trough_contract_mid = min(float(bar["l"]) for bar in option_window)
    peak_unrealized = (peak_contract_mid - entry_fill) * 100.0
    trough_unrealized = (trough_contract_mid - entry_fill) * 100.0
    years_exit = _years_to_expiry(snapshot.expiration_date, exit_bar["start_ct"], cfg.timezone)
    exit_iv = implied_volatility(
        right=snapshot.right,
        premium=exit_raw,
        spot=underlying_exit_px,
        strike=snapshot.strike,
        years_to_expiry=years_exit,
        risk_free_rate=0.04,
    )
    exit_delta = None if exit_iv is None else option_delta(
        right=snapshot.right,
        spot=underlying_exit_px,
        strike=snapshot.strike,
        years_to_expiry=years_exit,
        risk_free_rate=0.04,
        sigma=exit_iv,
    )
    return {
        "outcome_id": _stable_id("out", event_id, snapshot.option_symbol),
        "event_id": event_id,
        "option_symbol": snapshot.option_symbol,
        "exit_ts": exit_bar["start_ct"].isoformat(),
        "entry_fill": entry_fill,
        "exit_fill": exit_fill,
        "entry_bid": entry_raw,
        "entry_ask": entry_raw,
        "exit_bid": exit_raw,
        "exit_ask": exit_raw,
        "entry_delta": snapshot.delta,
        "exit_delta": exit_delta,
        "entry_iv": snapshot.iv,
        "exit_iv": exit_iv,
        "exit_reason": exit_reason,
        "underlying_entry_px": signal.underlying_price,
        "underlying_exit_px": underlying_exit_px,
        "underlying_peak_px": max(float(bar["h"]) for bar in stock_window),
        "underlying_trough_px": min(float(bar["l"]) for bar in stock_window),
        "peak_contract_mid": peak_contract_mid,
        "trough_contract_mid": trough_contract_mid,
        "max_favorable_excursion": peak_unrealized,
        "max_adverse_excursion": trough_unrealized,
        "holding_minutes": max((exit_bar["start_ct"] - entry_bar["start_ct"]).total_seconds() / 60.0, 1.0),
        "profit_lock_triggered": int(peak_unrealized > abs(trough_unrealized) and peak_unrealized > 0),
        "fees_estimate": 0.0,
        "entry_slippage_estimate": entry_friction,
        "exit_slippage_estimate": exit_friction,
        "split_bucket": _split_bucket(entry_bar["start_ct"].date().isoformat(), cfg),
        "outcome_json": _json_dumps(
            {
                "source": "alpaca_historical_stage_v1",
                "pricing_proxy": "option_1m_open_with_execution_friction",
                "bar_count": len(option_window),
                "stock_bar_count": len(stock_window),
            }
        ),
        "created_at": datetime.now(tz=ZoneInfo(cfg.timezone)).isoformat(),
    }


def _load_signal_rows(conn: sqlite3.Connection, start: str | None, end: str | None) -> list[sqlite3.Row]:
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    sql = [
        "SELECT * FROM research_input_signals WHERE 1=1",
    ]
    params: list[Any] = []
    if start:
        sql.append("AND session_date >= ?")
        params.append(start)
    if end:
        sql.append("AND session_date <= ?")
        params.append(end)
    sql.append("ORDER BY event_ts ASC")
    rows = conn.execute(" ".join(sql), params).fetchall()
    conn.row_factory = previous_factory
    return rows


def _signal_from_row(row: sqlite3.Row) -> UnderlyingSignal:
    return UnderlyingSignal(
        event_id=row["event_id"],
        symbol=row["symbol"],
        direction=row["direction"],
        event_ts=row["event_ts"],
        variant_id=row["variant_id"],
        confidence=row["confidence"],
        ref_horizon=row["ref_horizon"],
        include_today_or=row["include_today_or"],
        underlying_price=row["underlying_price"],
        underlying_stop_price=row["underlying_stop_price"],
        bar_open=row["bar_open"],
        bar_high=row["bar_high"],
        bar_low=row["bar_low"],
        bar_close=row["bar_close"],
        ema20=row["ema20"],
        ema20_slope=row["ema20_slope"],
        source_tag=row["source_tag"],
        notes_json=row["notes_json"],
    )


def _stage_signal(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    provider: HistoricalDataProvider,
    signal: UnderlyingSignal,
) -> dict[str, Any]:
    event_dt_ct = _parse_ts(signal.event_ts, cfg.timezone)
    session_date = event_dt_ct.date().isoformat()
    if session_date < HISTORICAL_OPTIONS_START:
        raise ValueError(f"Alpaca options history starts on {HISTORICAL_OPTIONS_START}; requested {session_date}")
    session_open_ct, _ = _regular_session_bounds(session_date, cfg)
    force_exit_dt_ct = _session_dt(session_date, cfg.options.force_exit_time, cfg.timezone)
    stock_start_ct = event_dt_ct - timedelta(days=10)
    stock_end_ct = force_exit_dt_ct + timedelta(minutes=_timeframe_to_minutes(cfg.underlying_signal.interval))
    stock_rows = provider.get_stock_bars(
        symbol=signal.symbol,
        start=_iso_utc(stock_start_ct),
        end=_iso_utc(stock_end_ct),
        timeframe=cfg.underlying_signal.interval.replace("m", "Min"),
        feed=cfg.market_data.stock_feed,
        adjustment="raw",
    )
    stock_bars = _filter_stock_bars(stock_rows, cfg)
    if not stock_bars:
        raise ValueError(f"No Alpaca stock bars found for {signal.symbol} around {signal.event_ts}")
    _store_underlying_bars(conn, signal.symbol, stock_bars, cfg)
    stock_path_rows = provider.get_stock_bars(
        symbol=signal.symbol,
        start=_iso_utc(session_open_ct),
        end=_iso_utc(force_exit_dt_ct + timedelta(minutes=1)),
        timeframe=DEFAULT_UNDERLYING_PATH_TIMEFRAME,
        feed=cfg.market_data.stock_feed,
        adjustment="raw",
    )
    stock_path_bars = _filter_stock_bars(stock_path_rows, cfg, timeframe_min=1)
    _store_underlying_bars(conn, signal.symbol, stock_path_bars, cfg, timeframe_min=1)
    event_bar = _find_event_bar(stock_bars, event_dt_ct, cfg)
    enriched_signal = UnderlyingSignal(
        event_id=signal.event_id or _stable_id(
            "evt",
            signal.symbol,
            signal.event_ts,
            signal.direction,
            signal.variant_id,
            signal.ref_horizon,
            signal.include_today_or,
        ),
        symbol=signal.symbol,
        direction=signal.direction,
        event_ts=signal.event_ts,
        variant_id=signal.variant_id,
        confidence=signal.confidence,
        ref_horizon=signal.ref_horizon,
        include_today_or=signal.include_today_or,
        underlying_price=(signal.underlying_price if signal.underlying_price is not None else float(event_bar["c"])),
        underlying_stop_price=signal.underlying_stop_price,
        bar_open=float(event_bar["o"]),
        bar_high=float(event_bar["h"]),
        bar_low=float(event_bar["l"]),
        bar_close=float(event_bar["c"]),
        ema20=(None if event_bar.get("ema20") is None else float(event_bar["ema20"])),
        ema20_slope=(None if event_bar.get("ema20_slope") is None else float(event_bar["ema20_slope"])),
        source_tag="alpaca_historical_stage_v1",
        notes_json=_json_dumps(
            {
                "event_bar_start_ct": event_bar["start_ct"].isoformat(),
                "event_bar_close_ct": event_bar["close_ct"].isoformat(),
                "force_exit_ct": force_exit_dt_ct.isoformat(),
            }
        ),
    )
    _upsert(
        conn,
        "research_input_signals",
        "event_id",
        {
            "event_id": enriched_signal.event_id,
            "symbol": enriched_signal.symbol,
            "session_date": session_date,
            "event_ts": enriched_signal.event_ts,
            "direction": enriched_signal.direction,
            "variant_id": enriched_signal.variant_id,
            "confidence": enriched_signal.confidence,
            "ref_horizon": enriched_signal.ref_horizon,
            "include_today_or": enriched_signal.include_today_or,
            "underlying_price": enriched_signal.underlying_price,
            "underlying_stop_price": enriched_signal.underlying_stop_price,
            "bar_open": enriched_signal.bar_open,
            "bar_high": enriched_signal.bar_high,
            "bar_low": enriched_signal.bar_low,
            "bar_close": enriched_signal.bar_close,
            "ema20": enriched_signal.ema20,
            "ema20_slope": enriched_signal.ema20_slope,
            "source_tag": enriched_signal.source_tag,
            "notes_json": enriched_signal.notes_json,
            "created_at": datetime.now(tz=ZoneInfo(cfg.timezone)).isoformat(),
        },
    )

    direction_right = required_right(enriched_signal.direction)
    exp_gte = (event_dt_ct.date() + timedelta(days=int(cfg.options.allowed_dte_min))).isoformat()
    exp_lte = (event_dt_ct.date() + timedelta(days=int(cfg.options.allowed_dte_max))).isoformat()
    strike_band_pct = DEFAULT_STRIKE_BAND_PCT
    strike_low = float(enriched_signal.underlying_price or 0.0) * (1.0 - strike_band_pct)
    strike_high = float(enriched_signal.underlying_price or 0.0) * (1.0 + strike_band_pct)
    contract_statuses = _contract_statuses_to_query(
        expiration_date_gte=exp_gte,
        expiration_date_lte=exp_lte,
        timezone_name=cfg.timezone,
    )
    contracts: list[dict[str, Any]] = []
    for contract_status in contract_statuses:
        contracts.extend(
            provider.list_option_contracts(
                underlying_symbol=enriched_signal.symbol,
                expiration_date_gte=exp_gte,
                expiration_date_lte=exp_lte,
                contract_type=direction_right,
                strike_price_gte=strike_low,
                strike_price_lte=strike_high,
                status=contract_status,
            )
        )
    historical_contracts = _dedupe_contract_rows(contracts)
    contract_symbols = [str(row["symbol"]).strip().upper() for row in historical_contracts]
    option_bars_map = provider.get_option_bars(
        symbols=contract_symbols,
        start=_iso_utc(session_open_ct),
        end=_iso_utc(force_exit_dt_ct + timedelta(minutes=5)),
        timeframe=DEFAULT_OPTION_BAR_TIMEFRAME,
    )
    conn.execute("DELETE FROM research_input_chain_snapshots WHERE event_id = ?", (enriched_signal.event_id,))
    conn.execute("DELETE FROM research_input_outcomes WHERE event_id = ?", (enriched_signal.event_id,))
    conn.execute("DELETE FROM research_input_option_bar_paths WHERE event_id = ?", (enriched_signal.event_id,))
    conn.execute("DELETE FROM research_input_underlying_bar_paths WHERE event_id = ?", (enriched_signal.event_id,))
    for path_row in _build_underlying_path_rows(
        event_id=enriched_signal.event_id or "",
        symbol=enriched_signal.symbol,
        underlying_bars=stock_path_bars,
        event_dt_ct=event_dt_ct,
        timeframe_min=1,
    ):
        _insert(conn, "research_input_underlying_bar_paths", path_row)
    staged_snapshots: list[OptionContractSnapshot] = []
    staged_outcomes = 0
    missing_entry_bars = 0
    for contract_row in historical_contracts:
        option_symbol = str(contract_row["symbol"]).strip().upper()
        option_bars = _annotate_option_bars(option_bars_map.get(option_symbol, []), cfg)
        entry_bar = _pick_entry_bar(option_bars, event_dt_ct)
        if entry_bar is None:
            missing_entry_bars += 1
            continue
        for path_row in _build_option_path_rows(
            event_id=enriched_signal.event_id or "",
            option_symbol=option_symbol,
            option_bars=option_bars,
            event_dt_ct=event_dt_ct,
        ):
            _insert(conn, "research_input_option_bar_paths", path_row)
        cumulative_volume = int(sum(float(bar["v"]) for bar in option_bars if bar["start_ct"] <= entry_bar["start_ct"]))
        snapshot = _build_contract_snapshot(contract_row, enriched_signal, entry_bar, cumulative_volume, cfg)
        chain_row = _build_chain_row(enriched_signal.event_id or "", enriched_signal, snapshot, entry_bar)
        _insert(conn, "research_input_chain_snapshots", chain_row)
        outcome_row = _build_outcome_row(
            event_id=enriched_signal.event_id or "",
            signal=enriched_signal,
            snapshot=snapshot,
            option_bars=option_bars,
            stock_bars=stock_bars,
            force_exit_dt_ct=force_exit_dt_ct,
            cfg=cfg,
        )
        if outcome_row is not None:
            _upsert(conn, "research_input_outcomes", "outcome_id", outcome_row)
            staged_outcomes += 1
        staged_snapshots.append(snapshot)
    conn.commit()
    evaluated, selected = select_contract(enriched_signal, staged_snapshots, cfg.options) if staged_snapshots else ([], None)
    passed = [row for row in evaluated if row.passed]
    return {
        "event_id": enriched_signal.event_id,
        "symbol": enriched_signal.symbol,
        "direction": enriched_signal.direction,
        "event_ts": enriched_signal.event_ts,
        "session_date": session_date,
        "underlying_price": enriched_signal.underlying_price,
        "underlying_stop_price": enriched_signal.underlying_stop_price,
        "contracts_listed": len(historical_contracts),
        "contracts_staged": len(staged_snapshots),
        "outcomes_staged": staged_outcomes,
        "missing_entry_bars": missing_entry_bars,
        "contracts_passing_filters": len(passed),
        "selected_contract": None if selected is None else selected.contract.option_symbol,
        "selected_selection_reason": None if selected is None else selected.selection_reason,
        "contract_statuses_queried": [status for status in contract_statuses if status is not None],
        "signal": asdict(enriched_signal),
    }


def stage_historical_event(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    symbol: str,
    direction: str,
    event_ts: str,
    underlying_stop_price: float | None = None,
    confidence: float | None = None,
    variant_id: str | None = None,
    ref_horizon: int | None = None,
    include_today_or: int | None = None,
    underlying_price: float | None = None,
) -> dict[str, Any]:
    provider = build_historical_provider(cfg)
    signal = UnderlyingSignal(
        symbol=str(symbol).strip().upper(),
        direction=str(direction).strip().upper(),  # type: ignore[arg-type]
        event_ts=str(event_ts).strip(),
        variant_id=(str(variant_id).strip() if variant_id else cfg.underlying_signal.variant_id),
        confidence=confidence,
        ref_horizon=ref_horizon,
        include_today_or=include_today_or,
        underlying_price=underlying_price,
        underlying_stop_price=underlying_stop_price,
    )
    return _stage_signal(conn, cfg, provider, signal)


def stage_historical_existing_signals(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    provider = build_historical_provider(cfg)
    rows = _load_signal_rows(conn, start=start, end=end)
    if limit is not None:
        rows = rows[: max(0, int(limit))]
    staged: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        signal = _signal_from_row(row)
        try:
            staged.append(_stage_signal(conn, cfg, provider, signal))
        except Exception as exc:
            errors.append(
                {
                    "event_id": signal.event_id,
                    "symbol": signal.symbol,
                    "event_ts": signal.event_ts,
                    "error": str(exc),
                }
            )
    return {
        "requested": len(rows),
        "staged": len(staged),
        "errors": errors,
        "results": staged,
    }


def stage_alpaca_event(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    symbol: str,
    direction: str,
    event_ts: str,
    underlying_stop_price: float | None = None,
    confidence: float | None = None,
    variant_id: str | None = None,
    ref_horizon: int | None = None,
    include_today_or: int | None = None,
    underlying_price: float | None = None,
) -> dict[str, Any]:
    return stage_historical_event(
        conn=conn,
        cfg=cfg,
        symbol=symbol,
        direction=direction,
        event_ts=event_ts,
        underlying_stop_price=underlying_stop_price,
        confidence=confidence,
        variant_id=variant_id,
        ref_horizon=ref_horizon,
        include_today_or=include_today_or,
        underlying_price=underlying_price,
    )


def stage_alpaca_existing_signals(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    return stage_historical_existing_signals(
        conn=conn,
        cfg=cfg,
        start=start,
        end=end,
        limit=limit,
    )
