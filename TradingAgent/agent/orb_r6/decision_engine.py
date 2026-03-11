from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List
import json

ENGINE_VERSION = "v1.0.0"


@dataclass(frozen=True)
class DecisionResult:
    decision: str      # LONG | SHORT | NO_TRADE
    confidence: float  # 0..1
    reasons: List[str]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def decide(features: Dict[str, float | int | str | None]) -> DecisionResult:
    direction = str(features.get("direction") or "")
    strength = float(features.get("breakout_strength") or 0.0)
    inflation = float(features.get("inflation_factor") or 0.0)
    overlap = float(features.get("or_overlap_pairs_pct") or 0.0)
    bias = float(features.get("mean_direction_bias") or 0.0)
    bias_cons = float(features.get("bias_consistency") or 0.0)
    wick_pen = float(features.get("wick_pen") or 0.0)
    close_pen = float(features.get("close_pen") or 0.0)
    also_count = int(features.get("also_count") or 0)
    include_today_or = int(features.get("include_today_or") or 0)

    score = 0.0
    reasons: List[str] = []

    # Strength
    if strength >= 0.15:
        score += 0.25; reasons.append("strong break")
    elif strength >= 0.08:
        score += 0.12; reasons.append("moderate break")
    else:
        score -= 0.10; reasons.append("weak break")

    if close_pen >= 0.03:
        score += 0.10; reasons.append("good close penetration")

    # Wick risk
    if wick_pen >= 0.40:
        score -= 0.18; reasons.append("wicky candle")
    elif 0 < wick_pen <= 0.20:
        score += 0.06; reasons.append("clean wick")

    # Regime
    if overlap >= 0.60:
        score += 0.10; reasons.append("clustered ORs")
    elif overlap <= 0.30:
        score -= 0.06; reasons.append("shifting ORs")

    if inflation > 2.25:
        score -= 0.06; reasons.append("stretched RR")
    elif 0 < inflation < 1.25:
        score += 0.04; reasons.append("tight RR")

    # Bias
    if bias_cons >= 0.60 and abs(bias) >= 0.05:
        if (direction == "UP" and bias > 0) or (direction == "DOWN" and bias < 0):
            score += 0.08; reasons.append("bias-aligned")
        else:
            score -= 0.04; reasons.append("bias-opposed")

    # Confluence
    if also_count >= 1:
        score += 0.06; reasons.append("multi-horizon")
    if also_count >= 2:
        score += 0.04; reasons.append("strong confluence")

    # Pre-OR context discount
    if include_today_or == 0:
        score -= 0.03; reasons.append("pre-OR context")

    conf = _clamp01(0.50 + score)

    if conf >= 0.62:
        decision = "LONG" if direction == "UP" else "SHORT"
    elif conf >= 0.54:
        decision = "LONG" if direction == "UP" else "SHORT"
        reasons.append("low conviction")
    else:
        decision = "NO_TRADE"
        reasons.append("insufficient edge")

    return DecisionResult(decision, conf, reasons)


def reasons_to_json(reasons: List[str]) -> str:
    return json.dumps(reasons, ensure_ascii=False)
