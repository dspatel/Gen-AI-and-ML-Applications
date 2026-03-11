from __future__ import annotations
from typing import List, Dict, Any
import itertools

def _overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)

def compute_or_overlap_counts(or_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return overlap stats across a list of daily OR rows.

    We report TWO notions of overlap:

    1) Adjacent overlap (neighbor sessions only):
       - compares day[i] with day[i+1] in the provided order (assumed chronological)
       - useful for "day-to-day continuity"

    2) All-pairs overlap:
       - compares every pair of sessions in the horizon
       - useful for "clustered vs dispersed" OR zones
    """
    # Be defensive: OR resolution may yield None rows (missing history)
    # or partially-populated dicts (schema evolution). Overlap metrics should
    # ignore invalid rows rather than crashing the pipeline.
    valid_rows: List[Dict[str, Any]] = []
    for r in (or_rows or []):
        if not isinstance(r, dict):
            continue
        lo = r.get("or_low")
        hi = r.get("or_high")
        if lo is None or hi is None:
            continue
        try:
            _ = float(lo)
            _ = float(hi)
        except Exception:
            continue
        valid_rows.append(r)

    if not valid_rows:
        return {
            "or_days": 0,
            "or_overlap_days_count": 0,
            "or_overlap_pairs_pct": 0.0,
            "or_overlap_adjacent_count": 0,
            "or_overlap_adjacent_total": 0,
            "or_overlap_adjacent_pct": 0.0,
        }

    n = len(valid_rows)
    intervals = [(float(r["or_low"]), float(r["or_high"])) for r in valid_rows]

    # Adjacent overlap (neighbor sessions only)
    adj_total = max(0, n - 1)
    adj_count = 0
    for i in range(adj_total):
        a = intervals[i]
        b = intervals[i + 1]
        if _overlap(a[0], a[1], b[0], b[1]):
            adj_count += 1

    # All-pairs overlap
    overlap_day = [False] * n
    overlap_pairs = 0
    total_pairs = n * (n - 1) // 2

    for (i, a), (j, b) in itertools.combinations(list(enumerate(intervals)), 2):
        if _overlap(a[0], a[1], b[0], b[1]):
            overlap_pairs += 1
            overlap_day[i] = True
            overlap_day[j] = True

    return {
        "or_days": n,
        "or_overlap_days_count": int(sum(overlap_day)),
        "or_overlap_pairs_pct": float(overlap_pairs / total_pairs) if total_pairs > 0 else 0.0,
        "or_overlap_adjacent_count": int(adj_count),
        "or_overlap_adjacent_total": int(adj_total),
        "or_overlap_adjacent_pct": float(adj_count / adj_total) if adj_total > 0 else 0.0,
    }
