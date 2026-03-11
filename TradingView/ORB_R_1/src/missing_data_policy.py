from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

@dataclass(frozen=True)
class CoverageEval:
    required_days: int
    available_days: int
    coverage_ratio: float
    is_valid: bool
    missing_dates: List[str]
    failure_reason: str

def evaluate_coverage(required_dates: Sequence[str], available_dates: Sequence[str], min_coverage_ratio: float) -> CoverageEval:
    required_set = list(dict.fromkeys(required_dates))  # preserve order, unique
    available_set = set(available_dates)
    missing = [d for d in required_set if d not in available_set]
    req = len(required_set)
    avail = req - len(missing)
    cov = (avail / req) if req > 0 else 0.0
    is_valid = cov >= float(min_coverage_ratio) if req > 0 else False

    if req == 0:
        reason = "no_required_dates"
    elif avail == 0:
        reason = "no_or_days_available"
    elif is_valid:
        reason = "ok"
    else:
        reason = f"coverage_below_threshold ({cov:.2f} < {min_coverage_ratio:.2f})"

    return CoverageEval(
        required_days=req,
        available_days=avail,
        coverage_ratio=round(cov, 6),
        is_valid=bool(is_valid),
        missing_dates=missing,
        failure_reason=reason,
    )
