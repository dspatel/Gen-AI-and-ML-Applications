from __future__ import annotations

import numpy as np
import pandas as pd

def _col(df: pd.DataFrame, low: str, up: str) -> str:
    if low in df.columns:
        return low
    if up in df.columns:
        return up
    raise KeyError(low)

def compute_day_behavior(session_df: pd.DataFrame, or_high: float, or_low: float) -> dict:
    """Compute behavior metrics for a session relative to that session's own OR."""
    if session_df is None or session_df.empty:
        return {}

    close_c = _col(session_df, "close", "Close")
    high_c = _col(session_df, "high", "High")
    low_c = _col(session_df, "low", "Low")

    closes = session_df[close_c]
    highs = session_df[high_c]
    lows = session_df[low_c]

    inside = float(((closes >= or_low) & (closes <= or_high)).mean())
    outside_up = float((closes > or_high).mean())
    outside_dn = float((closes < or_low).mean())

    bias = outside_up - outside_dn

    session_range = float(highs.max() - lows.min())
    or_width = float(or_high - or_low) if (or_high is not None and or_low is not None) else 0.0
    range_to_or = float(session_range / or_width) if or_width > 1e-12 else 0.0

    return {
        "inside_own_or_pct": inside,
        "outside_up_pct": outside_up,
        "outside_dn_pct": outside_dn,
        "direction_bias": float(bias),
        "range_to_or": float(range_to_or),
    }

def aggregate_behavior(day_rows: list[dict]) -> dict:
    rows = [r for r in (day_rows or []) if r]
    if not rows:
        return {}

    inside = np.median([r["inside_own_or_pct"] for r in rows])
    rto = np.median([r["range_to_or"] for r in rows])
    bias_vals = np.array([r["direction_bias"] for r in rows], dtype=float)

    mean_bias = float(np.mean(bias_vals))
    nonzero = bias_vals[bias_vals != 0]
    if nonzero.size == 0 or mean_bias == 0:
        consistency = 0.0
    else:
        consistency = float(np.mean(np.sign(nonzero) == np.sign(mean_bias)))

    return {
        "median_inside_own_or_pct": float(inside),
        "median_range_to_or": float(rto),
        "mean_direction_bias": float(mean_bias),
        "bias_consistency": float(consistency),
    }
