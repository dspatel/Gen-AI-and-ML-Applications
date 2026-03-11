
from __future__ import annotations

from statistics import median


def build_reference_range(or_rows: list[dict]) -> dict:
    """Build today's reference range from prior OR rows.

    ref_high = max(or_high)
    ref_low  = min(or_low)

    Also computes:
    - or_overlap_ratio: overlap(ref_range) / union(ref_range)
      (proxy for how aligned the opens are)
    - inflation_factor: ref_width / median(or_width)
      (how "stretched" the reference is versus a typical OR)
    """
    ors = [r for r in (or_rows or []) if r and r.get("or_width") is not None]
    if not ors:
        return {}

    highs = [r["or_high"] for r in ors]
    lows = [r["or_low"] for r in ors]
    widths = [r["or_width"] for r in ors if r["or_width"] > 0]

    ref_high = max(highs)
    ref_low = min(lows)
    ref_width = ref_high - ref_low

    # overlap ratio across the lookback ORs: intersection / union
    inter_high = min(highs)
    inter_low = max(lows)
    overlap = max(0.0, inter_high - inter_low)
    union = max(1e-12, ref_width)
    or_overlap_ratio = overlap / union

    med_or_width = median(widths) if widths else 0.0
    inflation_factor = (ref_width / med_or_width) if med_or_width > 1e-12 else 0.0

    return {
        "ref_high": float(ref_high),
        "ref_low": float(ref_low),
        "ref_width": float(ref_width),
        "or_overlap_ratio": float(or_overlap_ratio),
        "inflation_factor": float(inflation_factor),
    }
