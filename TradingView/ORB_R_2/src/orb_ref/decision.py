
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple
import yaml


@dataclass(frozen=True)
class DecisionResult:
    decision: str            # LONG | SHORT | NO_TRADE
    confidence: float        # 0..1
    reasons: List[str]


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def load_decision_rules(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _score_reference(metrics: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[float, List[str]]:
    inf = float(metrics.get("inflation_factor", 0.0) or 0.0)
    reasons = []
    # default neutral
    score = 0.6

    tight = rules.get("tight", {})
    stretched = rules.get("stretched", {})

    if tight and inf > 0 and inf <= float(tight.get("max_inflation_factor", 1.6)):
        score = float(tight.get("score", 1.0))
        reasons.append(str(tight.get("reason", "Tight reference range")))
    elif stretched and inf >= float(stretched.get("min_inflation_factor", 2.2)):
        score = float(stretched.get("score", 0.2))
        reasons.append(str(stretched.get("reason", "Stretched reference range")))

    return _clip01(score), reasons


def _score_regime(metrics: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[float, List[str]]:
    inside = float(metrics.get("median_inside_own_or_pct", 0.0) or 0.0)
    rto = float(metrics.get("median_range_to_or", 0.0) or 0.0)
    reasons = []
    score = 0.6

    trending = rules.get("trending", {})
    choppy = rules.get("choppy", {})

    if trending:
        if inside <= float(trending.get("max_inside_pct", 0.55)) and rto >= float(trending.get("min_range_to_or", 2.5)):
            score = float(trending.get("score", 1.0))
            reasons.append(str(trending.get("reason", "Expansion / trend regime")))
            return _clip01(score), reasons

    if choppy:
        if inside >= float(choppy.get("min_inside_pct", 0.70)) and rto <= float(choppy.get("max_range_to_or", 2.0)):
            score = float(choppy.get("score", 0.25))
            reasons.append(str(choppy.get("reason", "Choppy / mean-reversion regime")))

    return _clip01(score), reasons


def _score_bias(metrics: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[float, List[str]]:
    cons = float(metrics.get("bias_consistency", 0.0) or 0.0)
    reasons = []
    score = 0.6

    consistent = rules.get("consistent", {})
    mixed = rules.get("mixed", {})

    if consistent and cons >= float(consistent.get("min_consistency", 0.70)):
        score = float(consistent.get("score", 1.0))
        reasons.append(str(consistent.get("reason", "Directional bias consistent")))
    elif mixed and cons <= float(mixed.get("max_consistency", 0.55)):
        score = float(mixed.get("score", 0.35))
        reasons.append(str(mixed.get("reason", "Directional bias mixed")))

    return _clip01(score), reasons


def _score_breakout(metrics: Dict[str, Any], rules: Dict[str, Any]) -> Tuple[float, List[str]]:
    cp = float(metrics.get("close_pen", 0.0) or 0.0)
    bn = float(metrics.get("body_norm", 0.0) or 0.0)
    reasons = []
    score = 0.6

    strong = rules.get("strong", {})
    weak = rules.get("weak", {})

    if strong and cp >= float(strong.get("min_close_pen", 0.06)) and bn >= float(strong.get("min_body_norm", 0.05)):
        score = float(strong.get("score", 1.0))
        reasons.append(str(strong.get("reason", "Strong close breakout")))
    elif weak and cp <= float(weak.get("max_close_pen", 0.02)):
        score = float(weak.get("score", 0.30))
        reasons.append(str(weak.get("reason", "Weak close breakout")))

    return _clip01(score), reasons


def decide(metrics: Dict[str, Any], direction: str, rules_cfg: Dict[str, Any]) -> DecisionResult:
    """Rule-based decision & confidence.

    - direction drives LONG vs SHORT when a breakout exists.
    - confidence is a weighted blend of:
      reference quality, regime suitability, bias consistency, breakout quality.

    returns NO_TRADE if direction is missing/unknown.
    """
    direction = (direction or "").upper().strip()
    if direction not in ("UP", "DOWN"):
        return DecisionResult(decision="NO_TRADE", confidence=0.0, reasons=["No breakout direction"])

    weights = rules_cfg.get("weights", {}) or {}
    w_ref = float(weights.get("reference", 0.25))
    w_reg = float(weights.get("regime", 0.20))
    w_bias = float(weights.get("bias", 0.25))
    w_bq = float(weights.get("breakout_quality", 0.30))

    ref_s, ref_r = _score_reference(metrics, rules_cfg.get("reference", {}) or {})
    reg_s, reg_r = _score_regime(metrics, rules_cfg.get("regime", {}) or {})
    bias_s, bias_r = _score_bias(metrics, rules_cfg.get("bias", {}) or {})
    bq_s, bq_r = _score_breakout(metrics, rules_cfg.get("breakout_quality", {}) or {})

    score = (w_ref * ref_s) + (w_reg * reg_s) + (w_bias * bias_s) + (w_bq * bq_s)
    score = _clip01(score)

    # Convert to decision text
    decision = "LONG" if direction == "UP" else "SHORT"

    reasons = []
    for r in (ref_r + reg_r + bias_r + bq_r):
        if r and r not in reasons:
            reasons.append(r)

    # Always include a compact fallback reason if none triggered
    if not reasons:
        reasons = ["Mixed context"]

    return DecisionResult(decision=decision, confidence=score, reasons=reasons)
