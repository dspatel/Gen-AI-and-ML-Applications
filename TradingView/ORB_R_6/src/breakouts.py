from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

Direction = Literal["UP", "DOWN"]


@dataclass
class RRRow:
    horizon_days: int
    ref_high: float
    ref_low: float
    ref_width: float
    inflation_factor: Optional[float] = None
    or_overlap_pairs_pct: Optional[float] = None
    or_overlap_days_pct: Optional[float] = None
    median_inside_own_or_pct: Optional[float] = None
    mean_direction_bias: Optional[float] = None
    bias_consistency: Optional[float] = None


@dataclass
class HorizonState:
    # If armed=False, we suppress additional alerts until a reset condition happens
    armed: bool = True


def ensure_breakout_tables(conn: sqlite3.Connection) -> None:
    """Create/migrate breakout event tables."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS breakout_events (
            event_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            asof_date_cst TEXT NOT NULL,
            bar_open_ts_cst TEXT NOT NULL,
            bar_close_ts_cst TEXT NOT NULL,
            interval TEXT NOT NULL,
            orb_minutes INTEGER NOT NULL,

            direction TEXT NOT NULL,
            primary_horizon INTEGER NOT NULL,
            include_today_or INTEGER NOT NULL,
            trigger_type TEXT NOT NULL,

            ref_low REAL NOT NULL,
            ref_high REAL NOT NULL,
            ref_width REAL NOT NULL,

            candle_open REAL,
            candle_high REAL,
            candle_low REAL,
            candle_close REAL,

            decision TEXT,
            confidence REAL,
            reasons_json TEXT,
            engine_version TEXT,

            message TEXT,
            sent_to_discord INTEGER NOT NULL DEFAULT 0,
            created_at_cst TEXT NOT NULL
        );
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS event_horizon_metrics (
            event_id TEXT NOT NULL,
            horizon_days INTEGER NOT NULL,

            did_break INTEGER NOT NULL,
            break_rank INTEGER NOT NULL,

            ref_high REAL NOT NULL,
            ref_low REAL NOT NULL,
            ref_width REAL NOT NULL,
            inflation_factor REAL,

            or_overlap_pairs_pct REAL,
            or_overlap_days_pct REAL,
            median_inside_own_or_pct REAL,
            mean_direction_bias REAL,
            bias_consistency REAL,

            breakout_amt REAL,
            breakout_strength REAL,

            close_pen REAL,
            wick_pen REAL,
            body_norm REAL,
            range_norm REAL,
            close_pos REAL,
            upper_wick_ratio REAL,
            lower_wick_ratio REAL,
            clean_break INTEGER,

            PRIMARY KEY (event_id, horizon_days)
        );
        """
    )

    # --- migrations: add any missing columns safely ---
    def _cols(table: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info('{table}');").fetchall()}

    be_cols = _cols("breakout_events")
    def _add_be(coldef: str, name: str) -> None:
        nonlocal be_cols
        if name not in be_cols:
            conn.execute(f"ALTER TABLE breakout_events ADD COLUMN {coldef};")
            be_cols.add(name)

    _add_be("candle_open REAL", "candle_open")
    _add_be("candle_high REAL", "candle_high")
    _add_be("candle_low REAL", "candle_low")
    _add_be("candle_close REAL", "candle_close")
    _add_be("decision TEXT", "decision")
    _add_be("confidence REAL", "confidence")
    _add_be("reasons_json TEXT", "reasons_json")
    _add_be("engine_version TEXT", "engine_version")

    eh_cols = _cols("event_horizon_metrics")
    def _add_eh(coldef: str, name: str) -> None:
        nonlocal eh_cols
        if name not in eh_cols:
            conn.execute(f"ALTER TABLE event_horizon_metrics ADD COLUMN {coldef};")
            eh_cols.add(name)

    _add_eh("inflation_factor REAL", "inflation_factor")
    _add_eh("close_pen REAL", "close_pen")
    _add_eh("wick_pen REAL", "wick_pen")
    _add_eh("body_norm REAL", "body_norm")
    _add_eh("range_norm REAL", "range_norm")

    _add_eh("close_pos REAL", "close_pos")
    _add_eh("upper_wick_ratio REAL", "upper_wick_ratio")
    _add_eh("lower_wick_ratio REAL", "lower_wick_ratio")
    _add_eh("clean_break INTEGER", "clean_break")

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_breakout_events_natural
        ON breakout_events(
            symbol, asof_date_cst, bar_close_ts_cst,
            interval, orb_minutes,
            primary_horizon, include_today_or,
            trigger_type, direction
        );
        """
    )

    conn.commit()


def load_rr_rows(
    conn: sqlite3.Connection,
    symbol: str,
    asof_date_cst: str,
    orb_minutes: int,
    interval: str,
    include_today_or: int = 0,
) -> Dict[int, RRRow]:
    q = """
    SELECT horizon_days, ref_high, ref_low, ref_width,
           inflation_factor,
           or_overlap_pairs_pct, or_overlap_days_pct,
           median_inside_own_or_pct, mean_direction_bias, bias_consistency,
           is_complete
    FROM daily_reference_metrics
    WHERE symbol=? AND asof_date_cst=? AND orb_minutes=? AND interval=? AND include_today_or=?
    ORDER BY horizon_days;
    """
    df = pd.read_sql_query(q, conn, params=[symbol, asof_date_cst, orb_minutes, interval, include_today_or])
    if df.empty:
        return {}
    df = df[df["is_complete"] == 1]
    out: Dict[int, RRRow] = {}
    for _, r in df.iterrows():
        h = int(r["horizon_days"])
        out[h] = RRRow(
            horizon_days=h,
            ref_high=float(r["ref_high"]),
            ref_low=float(r["ref_low"]),
            ref_width=float(r["ref_width"]),
            inflation_factor=(float(r["inflation_factor"]) if r["inflation_factor"] is not None else None),
            or_overlap_pairs_pct=(float(r["or_overlap_pairs_pct"]) if r["or_overlap_pairs_pct"] is not None else None),
            or_overlap_days_pct=(float(r["or_overlap_days_pct"]) if r["or_overlap_days_pct"] is not None else None),
            median_inside_own_or_pct=(float(r["median_inside_own_or_pct"]) if r["median_inside_own_or_pct"] is not None else None),
            mean_direction_bias=(float(r["mean_direction_bias"]) if r["mean_direction_bias"] is not None else None),
            bias_consistency=(float(r["bias_consistency"]) if r["bias_consistency"] is not None else None),
        )
    return out



def load_broken_horizons(
    conn: sqlite3.Connection,
    symbol: str,
    asof_date_cst: str,
    interval: str,
    orb_minutes: int,
    include_today_or: int,
) -> Dict[int, str]:
    """Return horizons already broken for this session+phase.

    Returns: {horizon_days: first_bar_close_ts_cst}
    """
    q = """
    SELECT eh.horizon_days, MIN(be.bar_close_ts_cst) AS first_ts
    FROM breakout_events be
    JOIN event_horizon_metrics eh ON be.event_id = eh.event_id
    WHERE be.symbol=? AND be.asof_date_cst=? AND be.interval=? AND be.orb_minutes=?
      AND be.include_today_or=? AND eh.did_break=1
    GROUP BY eh.horizon_days
    ORDER BY eh.horizon_days;
    """
    df = pd.read_sql_query(q, conn, params=[symbol, asof_date_cst, interval, int(orb_minutes), int(include_today_or)])
    if df.empty:
        return {}
    return {int(r["horizon_days"]): str(r["first_ts"]) for _, r in df.iterrows()}

def evaluate_close_only_breakouts(
    close_price: float,
    rr_by_h: Dict[int, RRRow],
    state_by_h: Dict[int, HorizonState],
    horizons: List[int],
) -> Tuple[Optional[int], Dict[int, Dict[str, Any]]]:
    """Evaluate close-only breakouts across horizons.

    Returns (primary_horizon, broke_dict_by_h).
    broke[h] includes: direction, breakout_amt, breakout_strength.
    """
    broke: Dict[int, Dict[str, Any]] = {}

    for h in horizons:
        if h not in rr_by_h:
            continue
        if h not in state_by_h:
            state_by_h[h] = HorizonState(armed=True)
        if not state_by_h[h].armed:
            continue

        rr = rr_by_h[h]
        if close_price > rr.ref_high:
            amt = close_price - rr.ref_high
            strength = (amt / rr.ref_width) if rr.ref_width and rr.ref_width > 0 else 0.0
            broke[h] = {"direction": "UP", "breakout_amt": float(amt), "breakout_strength": float(strength)}
        elif close_price < rr.ref_low:
            amt = rr.ref_low - close_price
            strength = (amt / rr.ref_width) if rr.ref_width and rr.ref_width > 0 else 0.0
            broke[h] = {"direction": "DOWN", "breakout_amt": float(amt), "breakout_strength": float(strength)}

    if not broke:
        return None, {}

    # primary horizon = smallest horizon first (earliest signal)
    primary = min(broke.keys())

    # disarm all horizons that broke on this bar (simple re-arm v1)
    for h in broke.keys():
        state_by_h[h].armed = False

    return primary, broke


def insert_breakout_event(
    conn: sqlite3.Connection,
    symbol: str,
    asof_date_cst: str,
    bar_open_ts_cst: str,
    bar_close_ts_cst: str,
    interval: str,
    orb_minutes: int,
    direction: Direction,
    primary_horizon: int,
    include_today_or: int,
    trigger_type: str,
    ref_low: float,
    ref_high: float,
    ref_width: float,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    # optional fields at the end (IMPORTANT for python default rules)
    decision: Optional[str] = None,
    confidence: Optional[float] = None,
    reasons_json: Optional[str] = None,
    engine_version: Optional[str] = None,
    message: Optional[str] = None,
) -> Tuple[str, bool]:
    """Insert a breakout event idempotently.

    Returns (event_id, inserted_new).
    If an identical event (same natural key) already exists, returns its event_id with inserted_new=False.
    """
    event_id = str(uuid.uuid4())

    sql = """
    INSERT OR IGNORE INTO breakout_events (
        event_id, symbol, asof_date_cst,
        bar_open_ts_cst, bar_close_ts_cst,
        interval, orb_minutes,
        direction, primary_horizon, include_today_or, trigger_type,
        ref_low, ref_high, ref_width,
        candle_open, candle_high, candle_low, candle_close,
        decision, confidence, reasons_json, engine_version,
        message, sent_to_discord, created_at_cst
    ) VALUES (
        ?, ?, ?,
        ?, ?,
        ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, 0, datetime('now', 'localtime')
    );
    """

    cur = conn.execute(
        sql,
        [
            event_id, symbol, asof_date_cst,
            bar_open_ts_cst, bar_close_ts_cst,
            interval, int(orb_minutes),
            direction, int(primary_horizon), int(include_today_or), trigger_type,
            float(ref_low), float(ref_high), float(ref_width),
            float(candle_open), float(candle_high), float(candle_low), float(candle_close),
            decision, confidence, reasons_json, engine_version,
            message,
        ],
    )
    conn.commit()

    if cur.rowcount == 1:
        return event_id, True

    # Already exists: fetch the existing event_id (natural key)
    q = """
    SELECT event_id
    FROM breakout_events
    WHERE symbol=? AND asof_date_cst=? AND bar_close_ts_cst=?
      AND interval=? AND orb_minutes=?
      AND primary_horizon=? AND include_today_or=?
      AND trigger_type=? AND direction=?
    LIMIT 1;
    """
    row = conn.execute(
        q,
        [
            symbol, asof_date_cst, bar_close_ts_cst,
            interval, int(orb_minutes),
            int(primary_horizon), int(include_today_or),
            trigger_type, direction,
        ],
    ).fetchone()
    existing = row[0] if row else event_id
    return existing, False



def insert_event_horizon_metrics(
    conn: sqlite3.Connection,
    event_id: str,
    rr_by_h: Dict[int, RRRow],
    broke: Dict[int, Dict[str, Any]],
    primary_horizon: int,
    quality_by_h: Optional[Dict[int, Dict[str, float]]] = None,
) -> None:
    if quality_by_h is None:
        quality_by_h = {}

    # Determine break ranks: primary is 1, others 2.. by horizon order
    broke_h = sorted(broke.keys())
    ranks = {h: (1 if h == primary_horizon else 1 + (broke_h.index(h))) for h in broke_h}

    sql = """
    INSERT INTO event_horizon_metrics (
        event_id, horizon_days,
        did_break, break_rank,
        ref_high, ref_low, ref_width, inflation_factor,
        or_overlap_pairs_pct, or_overlap_days_pct,
        median_inside_own_or_pct, mean_direction_bias, bias_consistency,
        breakout_amt, breakout_strength,
        close_pen, wick_pen, body_norm, range_norm,
        close_pos, upper_wick_ratio, lower_wick_ratio, clean_break
    ) VALUES (
        ?, ?,
        ?, ?,
        ?, ?, ?, ?,
        ?, ?,
        ?, ?, ?,
        ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?
    )
    ON CONFLICT(event_id, horizon_days) DO UPDATE SET
        did_break=excluded.did_break,
        break_rank=excluded.break_rank,
        ref_high=excluded.ref_high,
        ref_low=excluded.ref_low,
        ref_width=excluded.ref_width,
        inflation_factor=excluded.inflation_factor,
        or_overlap_pairs_pct=excluded.or_overlap_pairs_pct,
        or_overlap_days_pct=excluded.or_overlap_days_pct,
        median_inside_own_or_pct=excluded.median_inside_own_or_pct,
        mean_direction_bias=excluded.mean_direction_bias,
        bias_consistency=excluded.bias_consistency,
        breakout_amt=excluded.breakout_amt,
        breakout_strength=excluded.breakout_strength,
        close_pen=excluded.close_pen,
        wick_pen=excluded.wick_pen,
        body_norm=excluded.body_norm,
        range_norm=excluded.range_norm,
        close_pos=excluded.close_pos,
        upper_wick_ratio=excluded.upper_wick_ratio,
        lower_wick_ratio=excluded.lower_wick_ratio,
        clean_break=excluded.clean_break;
    """

    for h, rr in rr_by_h.items():
        did_break = 1 if h in broke else 0
        break_rank = int(ranks[h]) if h in ranks else 0
        breakout_amt = float(broke[h]["breakout_amt"]) if h in broke else None
        breakout_strength = float(broke[h]["breakout_strength"]) if h in broke else None

        q = quality_by_h.get(h, {})
        conn.execute(
            sql,
            [
                event_id, int(h),
                int(did_break), int(break_rank),
                float(rr.ref_high), float(rr.ref_low), float(rr.ref_width),
                (float(rr.inflation_factor) if rr.inflation_factor is not None else None),
                rr.or_overlap_pairs_pct, rr.or_overlap_days_pct,
                rr.median_inside_own_or_pct, rr.mean_direction_bias, rr.bias_consistency,
                breakout_amt, breakout_strength,
                q.get("close_pen"), q.get("wick_pen"), q.get("body_norm"), q.get("range_norm"),
                q.get("close_pos"), q.get("upper_wick_ratio"), q.get("lower_wick_ratio"), (int(q.get("clean_break")) if q.get("clean_break") is not None else None),
            ],
        )

    conn.commit()
