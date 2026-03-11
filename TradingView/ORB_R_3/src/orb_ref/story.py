from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional
import yaml

@dataclass(frozen=True)
class StoryResult:
    story: str
    components: Dict[str, str]

def load_story_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _pick_bucket(x: float, low: float, high: float) -> str:
    if x <= low:
        return "low"
    if x >= high:
        return "high"
    return "mid"

def build_market_story(
    metrics: Dict[str, Any],
    decision: str,
    confidence: float,
    labels: Dict[str, Any],
    story_cfg: Dict[str, Any],
    use_icons: Optional[bool] = None,
) -> StoryResult:
    thresholds = story_cfg.get("thresholds", {}) or {}
    phrases = story_cfg.get("phrases", {}) or {}
    icons = story_cfg.get("icons", {}) or {}

    if use_icons is None:
        use_icons = bool(story_cfg.get("use_icons", True))

    pairs_pct = float(metrics.get("or_overlap_pairs_pct", 0.0) or 0.0)
    overlap_bucket = _pick_bucket(
        pairs_pct,
        low=float(thresholds.get("overlap_low_pairs_pct", 0.30)),
        high=float(thresholds.get("overlap_high_pairs_pct", 0.60)),
    )
    overlap_txt = (phrases.get("overlap", {}) or {}).get(overlap_bucket, "OR overlap")
    overlap_icon = (icons.get("overlap", {}) or {}).get(overlap_bucket, "")

    infl = float(metrics.get("inflation_factor", 0.0) or 0.0)
    infl_txt = (phrases.get("inflation", {}) or {}).get("normal", "normal range")
    infl_icon = (icons.get("inflation", {}) or {}).get("normal", "")
    if infl > 0 and infl <= float(thresholds.get("inflation_tight_max", 1.60)):
        infl_txt = (phrases.get("inflation", {}) or {}).get("tight", "tight range")
        infl_icon = (icons.get("inflation", {}) or {}).get("tight", "")
    elif infl >= float(thresholds.get("inflation_stretched_min", 2.20)):
        infl_txt = (phrases.get("inflation", {}) or {}).get("stretched", "stretched range")
        infl_icon = (icons.get("inflation", {}) or {}).get("stretched", "")

    conf_high = float(thresholds.get("confidence_high", 0.70))
    conf_low = float(thresholds.get("confidence_low", 0.45))
    if confidence >= conf_high:
        conf_bucket = "high"
    elif confidence <= conf_low:
        conf_bucket = "low"
    else:
        conf_bucket = "mid"
    conf_txt = (phrases.get("confidence", {}) or {}).get(conf_bucket, "confidence")
    decision_icon = (icons.get("decision", {}) or {}).get(str(decision), "")

    regime = str(labels.get("regime", "")).strip()
    bias = str(labels.get("direction_bias", "")).strip()
    strength = str(labels.get("breakout_strength", "")).strip()

    parts = []
    if use_icons and overlap_icon:
        parts.append(overlap_icon)
    parts.append(overlap_txt)

    if use_icons and infl_icon:
        parts.append(infl_icon)
    parts.append(infl_txt)

    if regime:
        parts.append(regime)
    if bias:
        parts.append(bias)
    if strength:
        parts.append(strength)

    d_txt = f"{decision} ({int(round(confidence*100))}%)"
    if use_icons and decision_icon:
        d_txt = f"{decision_icon} {d_txt}"
    d_txt = f"{d_txt} — {conf_txt}"
    parts.append(d_txt)

    story = " • ".join([p for p in parts if p])

    return StoryResult(
        story=story,
        components={
            "overlap_bucket": overlap_bucket,
            "inflation_factor": f"{infl:.2f}",
            "confidence_bucket": conf_bucket,
            "regime": regime,
            "bias": bias,
            "strength": strength,
            "decision": str(decision),
        },
    )
