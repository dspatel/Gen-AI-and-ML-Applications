from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config_loader import AppConfig
from .symbols import load_symbols


DEFAULT_R6_SOURCE_DB = "./artifacts/r6_stable/orb_core.sqlite"
MIN_STOP_DISTANCE_PCT = 0.001


@dataclass(frozen=True)
class R6EntryPolicy:
    variant_id: str
    confidence_min: float
    one_trade_per_day: bool
    flat_only: bool
    allow_long_pre_or: bool


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def parse_variant_policy(variant_id: str) -> R6EntryPolicy:
    raw = str(variant_id).strip()
    entry_part = raw.split("__", 1)[0]
    m = re.search(r"CONF(\d+)", entry_part)
    confidence_min = (float(int(m.group(1))) / 100.0) if m else 0.0
    one_trade_per_day = "LIMIT1" in entry_part and "UNLIMITED" not in entry_part
    flat_only = "FLAT_ONLY" in entry_part
    allow_long_pre_or = "NO_LONG_PREOR" not in entry_part
    return R6EntryPolicy(
        variant_id=raw,
        confidence_min=confidence_min,
        one_trade_per_day=one_trade_per_day,
        flat_only=flat_only,
        allow_long_pre_or=allow_long_pre_or,
    )


def _compute_stop_price(direction: str, candle_high: float, candle_low: float, candle_close: float) -> float:
    close_px = float(candle_close)
    min_stop_dist = max(0.01, close_px * MIN_STOP_DISTANCE_PCT)
    if str(direction).upper() == "LONG":
        raw_stop = float(candle_low) - 0.01
        return min(raw_stop, close_px - min_stop_dist)
    raw_stop = float(candle_high) + 0.01
    return max(raw_stop, close_px + min_stop_dist)


def _query_source_events(
    source_db_path: str,
    symbols: list[str],
    start: str,
    end: str,
) -> list[sqlite3.Row]:
    if not symbols:
        return []
    source_path = Path(source_db_path)
    if not source_path.is_absolute():
        source_path = (Path.cwd() / source_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"R6 source DB not found: {source_path}")
    conn = sqlite3.connect(str(source_path))
    conn.row_factory = sqlite3.Row
    sym_ph = ",".join(["?"] * len(symbols))
    sql = f"""
    SELECT
        be.event_id,
        be.symbol,
        be.asof_date_cst AS session_date,
        be.bar_close_ts_cst AS event_ts,
        be.direction,
        be.decision,
        be.confidence,
        be.primary_horizon,
        be.include_today_or,
        be.candle_open,
        be.candle_high,
        be.candle_low,
        be.candle_close,
        eh.ref_high,
        eh.ref_low,
        eh.ref_width,
        eh.inflation_factor,
        eh.or_overlap_pairs_pct
    FROM breakout_events be
    LEFT JOIN event_horizon_metrics eh
      ON be.event_id = eh.event_id
     AND be.primary_horizon = eh.horizon_days
    WHERE be.interval = '15m'
      AND be.orb_minutes = 30
      AND be.asof_date_cst >= ?
      AND be.asof_date_cst <= ?
      AND be.symbol IN ({sym_ph})
      AND be.decision IN ('LONG', 'SHORT')
    ORDER BY be.asof_date_cst, be.symbol, be.bar_close_ts_cst
    """
    rows = conn.execute(sql, [start, end, *symbols]).fetchall()
    conn.close()
    return rows


def seed_r6_signals(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    start: str,
    end: str,
    source_db_path: str = DEFAULT_R6_SOURCE_DB,
    variant_id: str | None = None,
) -> dict[str, Any]:
    policy = parse_variant_policy(variant_id or cfg.underlying_signal.variant_id)
    symbols = [str(symbol).strip().upper() for symbol in load_symbols(cfg.symbols)]
    rows = _query_source_events(source_db_path=source_db_path, symbols=symbols, start=start, end=end)

    accepted_rows: list[sqlite3.Row] = []
    rejected_counts = {
        "below_confidence": 0,
        "flat_only_filtered": 0,
        "no_long_preor_filtered": 0,
        "duplicate_same_day_symbol": 0,
    }
    seen_symbol_day: set[tuple[str, str]] = set()
    for row in rows:
        confidence = float(row["confidence"] or 0.0)
        if confidence < policy.confidence_min:
            rejected_counts["below_confidence"] += 1
            continue
        if policy.flat_only:
            overlap = row["or_overlap_pairs_pct"]
            inflation = row["inflation_factor"]
            is_flat = (
                overlap is not None
                and inflation is not None
                and float(overlap) >= 0.60
                and float(inflation) <= 1.25
            )
            if not is_flat:
                rejected_counts["flat_only_filtered"] += 1
                continue
        if not policy.allow_long_pre_or and str(row["decision"]).upper() == "LONG" and int(row["include_today_or"] or 0) == 0:
            rejected_counts["no_long_preor_filtered"] += 1
            continue
        symbol_day = (str(row["symbol"]).upper(), str(row["session_date"]))
        if policy.one_trade_per_day and symbol_day in seen_symbol_day:
            rejected_counts["duplicate_same_day_symbol"] += 1
            continue
        seen_symbol_day.add(symbol_day)
        accepted_rows.append(row)

    inserted = 0
    for row in accepted_rows:
        stop_price = _compute_stop_price(
            direction=str(row["decision"]),
            candle_high=float(row["candle_high"]),
            candle_low=float(row["candle_low"]),
            candle_close=float(row["candle_close"]),
        )
        conn.execute(
            """
            INSERT INTO research_input_signals (
                event_id, symbol, session_date, event_ts, direction, variant_id, confidence,
                ref_horizon, include_today_or, underlying_price, underlying_stop_price,
                bar_open, bar_high, bar_low, bar_close, ema20, ema20_slope, source_tag, notes_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(event_id) DO UPDATE SET
                symbol=excluded.symbol,
                session_date=excluded.session_date,
                event_ts=excluded.event_ts,
                direction=excluded.direction,
                variant_id=excluded.variant_id,
                confidence=excluded.confidence,
                ref_horizon=excluded.ref_horizon,
                include_today_or=excluded.include_today_or,
                underlying_price=excluded.underlying_price,
                underlying_stop_price=excluded.underlying_stop_price,
                bar_open=excluded.bar_open,
                bar_high=excluded.bar_high,
                bar_low=excluded.bar_low,
                bar_close=excluded.bar_close,
                source_tag=excluded.source_tag,
                notes_json=excluded.notes_json
            """,
            (
                str(row["event_id"]),
                str(row["symbol"]).upper(),
                str(row["session_date"]),
                str(row["event_ts"]),
                "BULLISH" if str(row["decision"]).upper() == "LONG" else "BEARISH",
                policy.variant_id,
                float(row["confidence"]) if row["confidence"] is not None else None,
                int(row["primary_horizon"]) if row["primary_horizon"] is not None else None,
                int(row["include_today_or"]) if row["include_today_or"] is not None else None,
                float(row["candle_close"]),
                float(stop_price),
                float(row["candle_open"]) if row["candle_open"] is not None else None,
                float(row["candle_high"]) if row["candle_high"] is not None else None,
                float(row["candle_low"]) if row["candle_low"] is not None else None,
                float(row["candle_close"]) if row["candle_close"] is not None else None,
                None,
                None,
                "r6_breakout_seed",
                _json_dumps(
                    {
                        "source_db_path": str(source_db_path),
                        "r6_event_direction": row["direction"],
                        "r6_decision": row["decision"],
                        "ref_high": row["ref_high"],
                        "ref_low": row["ref_low"],
                        "ref_width": row["ref_width"],
                        "inflation_factor": row["inflation_factor"],
                        "or_overlap_pairs_pct": row["or_overlap_pairs_pct"],
                    }
                ),
            ),
        )
        inserted += 1
    conn.commit()
    return {
        "source_db_path": str(source_db_path),
        "variant_id": policy.variant_id,
        "confidence_min": policy.confidence_min,
        "one_trade_per_day": policy.one_trade_per_day,
        "flat_only": policy.flat_only,
        "allow_long_pre_or": policy.allow_long_pre_or,
        "source_rows": len(rows),
        "seeded_signals": inserted,
        "rejected_counts": rejected_counts,
        "symbols": symbols,
        "start": start,
        "end": end,
    }
