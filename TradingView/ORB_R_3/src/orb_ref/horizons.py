from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
from datetime import date

import pandas as pd

from orb_ref.reference_range import build_reference_range
from orb_ref.lookback_behavior import compute_day_behavior, aggregate_behavior
from orb_ref.or_overlap import compute_or_overlap_counts


@dataclass(frozen=True)
class HorizonResult:
    horizon_days: int
    ref: Dict[str, Any]
    behavior: Dict[str, Any]
    overlap: Dict[str, Any]
    sessions_requested: int
    sessions_nonempty: int
    sessions_used: int
    sessions_missing_data: List[str]
    active: bool
    inactive_reason: Optional[str] = None


def build_horizon_results(
    or_rows_by_session: List[Dict[str, Any]],
    day_behaviors: List[Dict[str, Any]],
    horizons: List[int],
    min_sessions_required: int = 3,
) -> Dict[int, HorizonResult]:
    """Build reference/behavior/overlap per horizon from an ordered list of past OR rows.

    Input lists should be ordered from most recent backward or vice versa; we will take last N rows.
    We treat horizons as the number of sessions to include.
    """
    results: Dict[int, HorizonResult] = {}
    if not horizons:
        return results

    # ensure stable order: take most recent N from the end (assume or_rows_by_session sorted ascending by date)
    ors = list(or_rows_by_session)
    behs = list(day_behaviors)

    for h in sorted(set(int(x) for x in horizons)):
        if h <= 0:
            continue
        # take last h items
        or_slice = ors[-h:] if len(ors) >= h else ors[:]
        beh_slice = behs[-h:] if len(behs) >= h else behs[:]

        # Filter invalid OR rows (None/missing keys) so reference/overlap logic and
        # session counts reflect actual usable history.
        valid_or_slice = [
            r
            for r in or_slice
            if isinstance(r, dict)
            and r.get("or_low") is not None
            and r.get("or_high") is not None
            and r.get("or_width") is not None
        ]

        sessions_used = len(valid_or_slice)
        missing = []
        # We can't know exact missing session dates here; caller can pass it via or_rows if present
        ref = build_reference_range(valid_or_slice) if valid_or_slice else {}
        overlap = (
            compute_or_overlap_counts(valid_or_slice)
            if valid_or_slice
            else {"or_days": 0, "or_overlap_days_count": 0, "or_overlap_pairs_pct": 0.0}
        )
        behavior = aggregate_behavior(beh_slice) if beh_slice else {}

        active = bool(ref) and sessions_used >= min(min_sessions_required, h)
        reason = None
        if not active:
            if not ref:
                reason = "no_reference"
            else:
                reason = f"insufficient_sessions_used({sessions_used})"

        results[h] = HorizonResult(
            horizon_days=h,
            ref=ref,
            behavior=behavior,
            overlap=overlap,
            sessions_requested=h,
            sessions_nonempty=sessions_used,
            sessions_used=sessions_used,
            sessions_missing_data=missing,
            active=active,
            inactive_reason=reason,
        )
    return results
