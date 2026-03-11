from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .calendar import TradingCalendar
from .missing_data_policy import evaluate_coverage, CoverageEval
from .or_compute import resolve_daily_or
from .data_fetch import fetch_intraday_yfinance


def _compute_overlap_metrics(bands: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Compute overlap metrics across N OR bands.

    bands is a list of (low, high) tuples. Uses only the available OR days.
    """
    n = len(bands)
    if n < 2:
        return {
            "pairs_total": 0,
            "or_overlap_pairs_count": 0,
            "or_overlap_pairs_pct": 0.0,
            "or_overlap_days_count": 0,
            "or_overlap_days_pct": 0.0,
        }

    pairs_total = n * (n - 1) // 2
    overlap_pairs = 0
    day_overlaps = [False] * n

    for i in range(n):
        lo_i, hi_i = bands[i]
        for j in range(i + 1, n):
            lo_j, hi_j = bands[j]
            overlap = max(0.0, min(hi_i, hi_j) - max(lo_i, lo_j))
            if overlap > 0.0:
                overlap_pairs += 1
                day_overlaps[i] = True
                day_overlaps[j] = True

    overlap_days = sum(1 for v in day_overlaps if v)
    return {
        "pairs_total": int(pairs_total),
        "or_overlap_pairs_count": int(overlap_pairs),
        "or_overlap_pairs_pct": float(overlap_pairs) / float(pairs_total) if pairs_total else 0.0,
        "or_overlap_days_count": int(overlap_days),
        "or_overlap_days_pct": float(overlap_days) / float(n) if n else 0.0,
    }


def _compute_behavior_metrics(
    db,
    cal: TradingCalendar,
    symbol: str,
    session_dates: List[str],
    or_by_date: Dict[str, Tuple[float, float, float]],
    interval: str,
) -> Dict[str, Any]:
    """Compute behavior metrics across session_dates using each day's OWN OR.

    If intraday is missing for a day, skip that day and audit it.
    Returns aggregates + audit fields.
    """
    inside_pcts: List[float] = []
    range_to_or_vals: List[float] = []
    biases: List[float] = []

    missing: Dict[str, str] = {}
    for d_str in session_dates:
        sess = cal.session_for_date(date.fromisoformat(d_str))
        if sess is None:
            missing[d_str] = "not_a_session_day"
            continue

        or_low, or_high, or_width = or_by_date[d_str]
        if or_width is None or or_width <= 0:
            missing[d_str] = "or_width_nonpositive"
            continue

        start_utc = sess.open_ts.astimezone(ZoneInfo("UTC"))
        end_utc = sess.close_ts.astimezone(ZoneInfo("UTC"))
        try:
            df_utc = fetch_intraday_yfinance(symbol, start_utc=start_utc, end_utc=end_utc, interval=interval)
        except Exception as e:
            msg = str(e).replace("\n", " ").strip()
            if len(msg) > 140:
                msg = msg[:137] + "..."
            missing[d_str] = f"intraday_fetch_failed:{type(e).__name__}:{msg}"
            continue

        if df_utc is None or df_utc.empty:
            missing[d_str] = "intraday_empty"
            continue

        # Convert index to market tz for correctness
        df = df_utc.copy()
        df.index = df.index.tz_convert(cal.tz)

        closes = df["close"].dropna()
        if closes.empty:
            missing[d_str] = "no_closes"
            continue

        total = float(len(closes))
        inside = float(((closes >= or_low) & (closes <= or_high)).sum())
        above = float((closes > or_high).sum())
        below = float((closes < or_low).sum())

        inside_pct = inside / total
        above_pct = above / total
        below_pct = below / total
        direction_bias = above_pct - below_pct

        session_high = float(df["high"].max())
        session_low = float(df["low"].min())
        range_to_or = (session_high - session_low) / float(or_width)

        inside_pcts.append(inside_pct)
        range_to_or_vals.append(range_to_or)
        biases.append(direction_bias)

    required = len(session_dates)
    available = len(inside_pcts)

    if available == 0:
        return {
            "median_inside_own_or_pct": None,
            "median_range_to_or": None,
            "mean_direction_bias": None,
            "bias_consistency": None,
                "median_or_width": None,
                "inflation_factor": None,
            "behavior_days_required": int(required),
            "behavior_days_available": 0,
            "behavior_days_missing_json": json.dumps(list(missing.keys())),
            "behavior_failure_reason": "no_behavior_days_available",
        }

    mean_bias = float(sum(biases) / float(len(biases)))
    mean_sign = 1 if mean_bias > 0 else (-1 if mean_bias < 0 else 0)

    # Consistency: fraction of non-zero daily biases matching mean bias sign
    non_zero = [b for b in biases if b != 0]
    if mean_sign == 0 or len(non_zero) == 0:
        consistency = None
    else:
        consistency = float(sum(1 for b in non_zero if (1 if b > 0 else -1) == mean_sign)) / float(len(non_zero))

    return {
        "median_inside_own_or_pct": float(statistics.median(inside_pcts)),
        "median_range_to_or": float(statistics.median(range_to_or_vals)),
        "mean_direction_bias": mean_bias,
        "bias_consistency": consistency,
        "behavior_days_required": int(required),
        "behavior_days_available": int(available),
        "behavior_days_missing_json": json.dumps(list(missing.keys())),
        "behavior_failure_reason": "" if len(missing) == 0 else "partial_behavior_days_missing",
    }

def _today_or_ready(cal: TradingCalendar, d: date, orb_minutes: int) -> bool:
    """True if OR window for date d is complete *as of now* (in market tz)."""
    sess = cal.session_for_date(d)
    if sess is None:
        return False
    now_local = datetime.now(tz=cal.tz)
    or_end = sess.open_ts + timedelta(minutes=int(orb_minutes))
    return now_local >= or_end

def compute_reference_ranges(
    db,
    cal: TradingCalendar,
    asof_date: date,
    symbol: str,
    horizons: Sequence[int],
    include_today_or: bool,
    interval: str,
    orb_minutes: int,
    min_coverage_ratio: float,
) -> List[Dict[str, Any]]:
    """Compute RR per horizon independently. Never raises for missing data."""
    results: List[Dict[str, Any]] = []

    # Determine whether today's OR can be included (live-safe)
    today_ready = _today_or_ready(cal, asof_date, orb_minutes) if include_today_or else False
    used_today = bool(include_today_or and today_ready)

    for h in horizons:
        # required dates = last h sessions prior to asof_date, plus asof_date if include_today && ready
        prior = cal.previous_sessions(asof=asof_date, n=int(h))
        required_dates = [d.isoformat() for d in prior]
        if used_today:
            required_dates.append(asof_date.isoformat())

        available_dates: List[str] = []
        or_highs: List[float] = []
        or_lows: List[float] = []
        missing_reasons: Dict[str, str] = {}

        # DB-first OR resolution per required date
        for d_str in required_dates:
            d = date.fromisoformat(d_str)
            row, reason = resolve_daily_or(db, cal, symbol, d, interval=interval, orb_minutes=orb_minutes)
            if row and row.get("or_high") is not None and row.get("or_low") is not None:
                available_dates.append(d_str)
                or_highs.append(float(row["or_high"]))
                or_lows.append(float(row["or_low"]))
            else:
                missing_reasons[d_str] = reason

        cov_eval: CoverageEval = evaluate_coverage(required_dates, available_dates, float(min_coverage_ratio))

        if or_highs and or_lows:
            ref_high = max(or_highs)
            ref_low = min(or_lows)
            ref_width = ref_high - ref_low
        else:
            ref_high = None
            ref_low = None
            ref_width = None

        # failure reason: prioritize coverage reason, but include top missing reasons summary
        reason = cov_eval.failure_reason
        if cov_eval.missing_dates:
            # compact summary: date->reason for up to 3 missing
            parts = []
            for md in cov_eval.missing_dates[:3]:
                parts.append(f"{md}:{missing_reasons.get(md,'missing')}")
            if len(cov_eval.missing_dates) > 3:
                parts.append(f"(+{len(cov_eval.missing_dates)-3} more)")
            reason = f"{reason} | missing: " + ", ".join(parts)

        # Extra metrics (do not affect RR validity):
        # - OR overlap metrics computed from available OR bands
        # - Behavior metrics computed from intraday closes relative to each day's own OR
        if available_dates:
            bands = list(zip(or_lows, or_highs))
            overlap_metrics = _compute_overlap_metrics(bands)
            or_by_date = {
                d: (float(or_lows[i]), float(or_highs[i]), float(or_highs[i] - or_lows[i]))
                for i, d in enumerate(available_dates)
            }
            behavior_metrics = _compute_behavior_metrics(
                db=db,
                cal=cal,
                symbol=symbol,
                session_dates=available_dates,
                or_by_date=or_by_date,
                interval=interval,
            )
            # Additional RR shape metrics
            or_widths = [float(or_highs[i] - or_lows[i]) for i in range(len(or_highs))]
            if or_widths and ref_width is not None and float(ref_width) > 0:
                median_or_width = float(statistics.median(or_widths))
                inflation_factor = float(ref_width) / median_or_width if median_or_width > 0 else None
            else:
                median_or_width = None
                inflation_factor = None

            # Compatibility alias (used by label_engine bins)
            overlap_metrics['or_overlap_ratio'] = float(overlap_metrics.get('or_overlap_pairs_pct', 0.0) or 0.0)
            behavior_metrics['median_or_width'] = median_or_width
            behavior_metrics['inflation_factor'] = inflation_factor

        else:
            overlap_metrics = _compute_overlap_metrics([])
            overlap_metrics['or_overlap_ratio'] = 0.0
            behavior_metrics = {
                "median_inside_own_or_pct": None,
                "median_range_to_or": None,
                "mean_direction_bias": None,
                "bias_consistency": None,
                "median_or_width": None,
                "inflation_factor": None,
                "behavior_days_required": 0,
                "behavior_days_available": 0,
                "behavior_days_missing_json": json.dumps([]),
                "behavior_failure_reason": "no_or_days_available",
            }

        row_out = {
            "asof_date": asof_date.isoformat(),
            "symbol": symbol,
            "horizon_days": int(h),

            "ref_high": ref_high,
            "ref_low": ref_low,
            "ref_width": ref_width,

            # overlap + behavior
            **overlap_metrics,
            **behavior_metrics,

            "required_days": cov_eval.required_days,
            "available_days": cov_eval.available_days,
            "coverage_ratio": cov_eval.coverage_ratio,
            "is_valid": 1 if cov_eval.is_valid else 0,
            "missing_or_dates_json": json.dumps(cov_eval.missing_dates),
            "failure_reason": reason,

            "used_today_or": 1 if used_today else 0,
            "today_or_ready": 1 if today_ready else 0,

            "interval": interval,
            "orb_minutes": int(orb_minutes),
            "include_today_or": 1 if include_today_or else 0,

            "created_at": None,
        }

        # Upsert immediately; never let DB error stop tracker/tools
        try:
            db.upsert_rr(row_out)
        except Exception as e:
            # keep going; embed DB error into failure_reason for audit visibility
            row_out["failure_reason"] = f"{row_out['failure_reason']} | rr_db_write_failed:{type(e).__name__}"

        results.append(row_out)

    return results
