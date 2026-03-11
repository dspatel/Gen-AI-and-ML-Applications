from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Tuple, Dict
from zoneinfo import ZoneInfo
import sqlite3
import math
import statistics

import pandas as pd

CST = ZoneInfo("America/Chicago")

@dataclass(frozen=True)
class ReferenceMetricsRow:
    symbol: str
    asof_date_cst: str
    horizon_days: int
    orb_minutes: int
    interval: str
    include_today_or: int

    ref_high: Optional[float]
    ref_low: Optional[float]
    ref_width: Optional[float]
    inflation_factor: Optional[float]

    sessions_required: int
    sessions_available: int
    sessions_missing: int
    is_complete: int
    used_start_date_cst: Optional[str]
    used_end_date_cst: Optional[str]

    or_width_mean: Optional[float]
    or_width_median: Optional[float]
    or_width_min: Optional[float]
    or_width_max: Optional[float]

    pairs_total: int
    or_overlap_pairs_count: int
    or_overlap_pairs_pct: Optional[float]
    or_overlap_days_count: int
    or_overlap_days_pct: Optional[float]
    overlap_amount_median: Optional[float]

    or_mid_median: Optional[float]
    or_mid_std: Optional[float]
    or_mid_range: Optional[float]
    or_mid_drift_slope: Optional[float]

    median_inside_own_or_pct: Optional[float]
    median_range_to_or: Optional[float]
    mean_direction_bias: Optional[float]
    bias_consistency: Optional[float]

    ref_high_day_count: int
    ref_low_day_count: int
    ref_extreme_concentration: Optional[float]

    computed_at_cst: str


def ensure_daily_reference_metrics_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_reference_metrics (
            symbol TEXT NOT NULL,
            asof_date_cst TEXT NOT NULL,
            horizon_days INTEGER NOT NULL,
            orb_minutes INTEGER NOT NULL,
            interval TEXT NOT NULL,
            include_today_or INTEGER NOT NULL,

            ref_high REAL,
            ref_low REAL,
            ref_width REAL,
            inflation_factor REAL,

            sessions_required INTEGER NOT NULL,
            sessions_available INTEGER NOT NULL,
            sessions_missing INTEGER NOT NULL,
            is_complete INTEGER NOT NULL,
            used_start_date_cst TEXT,
            used_end_date_cst TEXT,

            or_width_mean REAL,
            or_width_median REAL,
            or_width_min REAL,
            or_width_max REAL,

            pairs_total INTEGER NOT NULL,
            or_overlap_pairs_count INTEGER NOT NULL,
            or_overlap_pairs_pct REAL,
            or_overlap_days_count INTEGER NOT NULL,
            or_overlap_days_pct REAL,
            overlap_amount_median REAL,

            or_mid_median REAL,
            or_mid_std REAL,
            or_mid_range REAL,
            or_mid_drift_slope REAL,

            median_inside_own_or_pct REAL,
            median_range_to_or REAL,
            mean_direction_bias REAL,
            bias_consistency REAL,

            ref_high_day_count INTEGER NOT NULL,
            ref_low_day_count INTEGER NOT NULL,
            ref_extreme_concentration REAL,

            computed_at_cst TEXT NOT NULL,

            PRIMARY KEY (symbol, asof_date_cst, horizon_days, orb_minutes, interval, include_today_or)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_reference_metrics_symbol_asof
        ON daily_reference_metrics(symbol, asof_date_cst);
        """
    )
    conn.commit()


def upsert_daily_reference_metrics(conn: sqlite3.Connection, rows: List[ReferenceMetricsRow]) -> int:
    """Upsert rows into daily_reference_metrics (robust to schema changes).

    We read the live table schema, then build INSERT/UPDATE dynamically.
    This prevents 'N values for M columns' errors when new columns are added.
    """
    if not rows:
        return 0

    table_cols = [r[1] for r in conn.execute("PRAGMA table_info('daily_reference_metrics');").fetchall()]
    if not table_cols:
        raise RuntimeError("daily_reference_metrics table not found (schema init failed).")

    pk = {"symbol", "asof_date_cst", "horizon_days", "orb_minutes", "interval", "include_today_or"}

    inserted = 0
    for r in rows:
        d = asdict(r)

        # Ensure we provide a value for every table column (None if missing)
        cols = table_cols
        values = [d.get(c, None) for c in cols]

        placeholders = ", ".join(["?"] * len(cols))
        col_sql = ", ".join(cols)

        set_cols = [c for c in cols if c not in pk]
        set_sql = ", ".join([f"{c}=excluded.{c}" for c in set_cols])

        sql = f"""
        INSERT INTO daily_reference_metrics ({col_sql})
        VALUES ({placeholders})
        ON CONFLICT(symbol, asof_date_cst, horizon_days, orb_minutes, interval, include_today_or)
        DO UPDATE SET {set_sql};
        """
        conn.execute(sql, values)
        inserted += 1

    conn.commit()
    return inserted


def _safe_median(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return float(statistics.median(vals))


def _safe_mean(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return float(sum(vals) / len(vals))


def _safe_std(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    if len(vals) < 2:
        return None
    return float(statistics.stdev(vals))


def _linear_slope(y: List[float]) -> Optional[float]:
    y = [v for v in y if v is not None and not math.isnan(v)]
    n = len(y)
    if n < 2:
        return None
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    den = sum((xi - x_mean) ** 2 for xi in x)
    if den == 0:
        return None
    return float(num / den)


def _compute_overlap_metrics(or_lows: List[float], or_highs: List[float]) -> Tuple[int, int, Optional[float], int, Optional[float], Optional[float]]:
    n = len(or_lows)
    pairs_total = n * (n - 1) // 2
    if n < 2:
        return pairs_total, 0, None, 0, None, None

    overlap_pairs = 0
    overlap_amounts: List[float] = []
    overlaps_any = [False] * n

    for i in range(n):
        for j in range(i + 1, n):
            overlap = max(0.0, min(or_highs[i], or_highs[j]) - max(or_lows[i], or_lows[j]))
            if overlap > 0:
                overlap_pairs += 1
                overlap_amounts.append(overlap)
                overlaps_any[i] = True
                overlaps_any[j] = True

    overlap_pairs_pct = (overlap_pairs / pairs_total) if pairs_total > 0 else None
    overlap_days = sum(1 for x in overlaps_any if x)
    overlap_days_pct = (overlap_days / n) if n > 0 else None
    overlap_amount_median = _safe_median(overlap_amounts) if overlap_amounts else None

    return pairs_total, overlap_pairs, overlap_pairs_pct, overlap_days, overlap_days_pct, overlap_amount_median


def _day_behavior_from_candles(conn: sqlite3.Connection, symbol: str, interval: str, cst_date: str, or_low: float, or_high: float) -> Optional[Tuple[float, float, float]]:
    q = """
    SELECT close
    FROM candles
    WHERE symbol = ? AND interval = ? AND cst_date = ?
    ORDER BY open_ts_utc
    """
    df = pd.read_sql_query(q, conn, params=[symbol, interval, cst_date])
    if df.empty:
        return None
    closes = df["close"].astype(float).tolist()
    n = len(closes)
    if n == 0:
        return None
    inside = sum(1 for c in closes if (c >= or_low and c <= or_high))
    above = sum(1 for c in closes if (c > or_high))
    below = sum(1 for c in closes if (c < or_low))
    return inside / n, above / n, below / n


def compute_reference_metrics_for_asof(
    conn: sqlite3.Connection,
    symbol: str,
    asof_date_cst: str,
    horizons: List[int],
    orb_minutes: int,
    interval: str,
    session_dates_cst: List[str],
    include_today_or: int = 0,
) -> Tuple[int, int]:
    ensure_daily_reference_metrics_table(conn)

    all_dates_sorted = list(session_dates_cst)
    # Prior sessions are strictly before the as-of session date.
    prior_only = [d for d in all_dates_sorted if d < asof_date_cst]
    include_today = 1 if include_today_or == 1 else 0
    max_h = max(horizons) if horizons else 0
    # Dates we may need OR rows for (DB-first): last max_h prior sessions, plus as-of date when include_today_or=1.
    candidate_dates = prior_only[-max_h:] if max_h > 0 else []
    if include_today == 1 and asof_date_cst in all_dates_sorted:
        candidate_dates = candidate_dates + [asof_date_cst]

    # Pull OR rows for all prior candidate dates
    if candidate_dates:
        ph = ",".join(["?"] * len(candidate_dates))
        q_or = f"""
        SELECT symbol, session_date_cst AS cst_date, or_high, or_low, or_range AS or_width,
               ((or_high + or_low)/2.0) AS or_mid,
               session_range
        FROM opening_ranges
        WHERE symbol = ? AND interval = ? AND or_minutes = ?
          AND session_date_cst IN ({ph})
        """
        prior_or_df = pd.read_sql_query(q_or, conn, params=[symbol, interval, orb_minutes] + candidate_dates)
    else:
        prior_or_df = pd.DataFrame()

    by_date: Dict[str, Dict[str, float]] = {}
    if not prior_or_df.empty:
        for _, r in prior_or_df.iterrows():
            by_date[str(r["cst_date"])] = {
                "or_high": float(r["or_high"]),
                "or_low": float(r["or_low"]),
                "or_width": float(r["or_width"]),
                "or_mid": float(r["or_mid"]),
                "session_range": float(r["session_range"]) if r["session_range"] is not None else float("nan"),
            }

    rows: List[ReferenceMetricsRow] = []
    incomplete_count = 0
    now_cst = datetime.now(CST).isoformat()

    for H in sorted(horizons):
        # Build per-horizon target session list.
        # candidate_dates contains the OR dates we loaded from opening_ranges.
        # We interpret it as prior sessions (and optionally include asof_date when include_today_or=1).
        prior_sessions_all = candidate_dates[:-1] if (include_today_or and candidate_dates and candidate_dates[-1] == asof_date_cst) else list(candidate_dates)
        base_prior = prior_sessions_all[-H:] if len(prior_sessions_all) >= H else list(prior_sessions_all)
        target_dates = base_prior + ([asof_date_cst] if include_today_or else [])
        sessions_required = H + (1 if include_today_or else 0)
        usable_dates = [d for d in target_dates if d in by_date]
        sessions_available = len(usable_dates)
        sessions_missing = sessions_required - sessions_available
        is_complete = 1 if sessions_available == sessions_required else 0
        if not is_complete:
            incomplete_count += 1

        used_start = usable_dates[0] if usable_dates else None
        used_end = usable_dates[-1] if usable_dates else None

        if sessions_available == 0:
            rows.append(ReferenceMetricsRow(
                symbol=symbol, asof_date_cst=asof_date_cst, horizon_days=H,
                orb_minutes=orb_minutes, interval=interval, include_today_or=include_today_or,
                ref_high=None, ref_low=None, ref_width=None, inflation_factor=None,
                sessions_required=sessions_required, sessions_available=0, sessions_missing=sessions_missing, is_complete=0,
                used_start_date_cst=None, used_end_date_cst=None,
                or_width_mean=None, or_width_median=None, or_width_min=None, or_width_max=None,
                pairs_total=0, or_overlap_pairs_count=0, or_overlap_pairs_pct=None,
                or_overlap_days_count=0, or_overlap_days_pct=None, overlap_amount_median=None,
                or_mid_median=None, or_mid_std=None, or_mid_range=None, or_mid_drift_slope=None,
                median_inside_own_or_pct=None, median_range_to_or=None, mean_direction_bias=None, bias_consistency=None,
                ref_high_day_count=0, ref_low_day_count=0, ref_extreme_concentration=None,
                computed_at_cst=now_cst
            ))
            continue

        or_highs = [by_date[d]["or_high"] for d in usable_dates]
        or_lows = [by_date[d]["or_low"] for d in usable_dates]
        or_widths = [by_date[d]["or_width"] for d in usable_dates]
        or_mids = [by_date[d]["or_mid"] for d in usable_dates]

        ref_high = max(or_highs)
        ref_low = min(or_lows)
        ref_width = ref_high - ref_low
        median_or_width = _safe_median(or_widths)
        inflation_factor = (float(ref_width) / median_or_width) if (median_or_width and median_or_width > 0) else None

        or_width_mean = _safe_mean(or_widths)
        or_width_median = _safe_median(or_widths)
        or_width_min = float(min(or_widths))
        or_width_max = float(max(or_widths))

        pairs_total, overlap_pairs, overlap_pairs_pct, overlap_days, overlap_days_pct, overlap_amount_median = _compute_overlap_metrics(or_lows, or_highs)

        or_mid_median = _safe_median(or_mids)
        or_mid_std = _safe_std(or_mids)
        or_mid_range = float(max(or_mids) - min(or_mids))
        or_mid_drift_slope = _linear_slope(or_mids)

        inside_pcts: List[float] = []
        range_to_ors: List[float] = []
        biases: List[float] = []

        for d in usable_dates:
            bhv = _day_behavior_from_candles(conn, symbol, interval, d, by_date[d]["or_low"], by_date[d]["or_high"])
            if bhv is None:
                continue
            inside, above, below = bhv
            inside_pcts.append(inside)
            sr = by_date[d]["session_range"]
            ow = by_date[d]["or_width"]
            if ow and ow > 0 and not math.isnan(sr):
                range_to_ors.append(sr / ow)
            biases.append(above - below)

        median_inside = _safe_median(inside_pcts)
        median_range_to_or = _safe_median(range_to_ors)
        mean_bias = _safe_mean(biases)

        bias_consistency = None
        if mean_bias is not None:
            sign = 1 if mean_bias > 0 else (-1 if mean_bias < 0 else 0)
            if sign != 0:
                non_zero = [b for b in biases if b != 0]
                if non_zero:
                    match = sum(1 for b in non_zero if (b > 0 and sign > 0) or (b < 0 and sign < 0))
                    bias_consistency = match / len(non_zero)

        tol = 1e-8
        ref_high_day_count = sum(1 for v in or_highs if abs(v - ref_high) <= tol)
        ref_low_day_count = sum(1 for v in or_lows if abs(v - ref_low) <= tol)
        ref_extreme_concentration = max(ref_high_day_count, ref_low_day_count) / sessions_available

        rows.append(ReferenceMetricsRow(
            symbol=symbol, asof_date_cst=asof_date_cst, horizon_days=H,
            orb_minutes=orb_minutes, interval=interval, include_today_or=include_today_or,
            ref_high=float(ref_high), ref_low=float(ref_low), ref_width=float(ref_width), inflation_factor=(float(inflation_factor) if inflation_factor is not None else None),
            sessions_required=sessions_required, sessions_available=sessions_available, sessions_missing=sessions_missing,
            is_complete=is_complete, used_start_date_cst=used_start, used_end_date_cst=used_end,
            or_width_mean=or_width_mean, or_width_median=or_width_median, or_width_min=or_width_min, or_width_max=or_width_max,
            pairs_total=pairs_total, or_overlap_pairs_count=overlap_pairs, or_overlap_pairs_pct=overlap_pairs_pct,
            or_overlap_days_count=overlap_days, or_overlap_days_pct=overlap_days_pct, overlap_amount_median=overlap_amount_median,
            or_mid_median=or_mid_median, or_mid_std=or_mid_std, or_mid_range=or_mid_range, or_mid_drift_slope=or_mid_drift_slope,
            median_inside_own_or_pct=median_inside, median_range_to_or=median_range_to_or,
            mean_direction_bias=mean_bias, bias_consistency=bias_consistency,
            ref_high_day_count=ref_high_day_count, ref_low_day_count=ref_low_day_count,
            ref_extreme_concentration=ref_extreme_concentration,
            computed_at_cst=now_cst
        ))

    upserted = upsert_daily_reference_metrics(conn, rows)
    return upserted, incomplete_count
