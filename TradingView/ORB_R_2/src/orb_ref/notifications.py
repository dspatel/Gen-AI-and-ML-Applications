from __future__ import annotations

from typing import Dict, Any
import yaml

DEFAULT_NUMERIC_KEYS = {
    # intensity
    "close_pen": 0.0,
    "wick_pen": 0.0,
    "body_norm": 0.0,
    "range_norm": 0.0,
    # reference / overlap
    "inflation_factor": 0.0,
    "or_overlap_pairs_pct": 0.0,
    "or_overlap_adjacent_pct": 0.0,
}

DEFAULT_INT_KEYS = {
    "or_overlap_adjacent_count": 0,
    "or_overlap_adjacent_total": 0,
    "or_overlap_days_count": 0,
    "or_days": 0,
    "confidence_pct": 0,
    "horizon_days": 0,
}

DEFAULT_STR_KEYS = {
    "decision": "N/A",
    "decision_reasons": "",
    "broken_horizons_before": "",
    "broken_horizons_after": "",
    "simultaneous_horizons": "",
    "story": "",
    "direction": "",
    "symbol": "",
    "event_time": "",
}

def load_templates(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class _SafeDict(dict):
    def __missing__(self, key):
        return "N/A"

def _prepare_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    p = dict(payload)

    # numeric defaults (also coerce None -> default)
    for k, v in DEFAULT_NUMERIC_KEYS.items():
        if p.get(k) is None:
            p[k] = v
    for k, v in DEFAULT_INT_KEYS.items():
        if p.get(k) is None:
            p[k] = v
    for k, v in DEFAULT_STR_KEYS.items():
        if p.get(k) is None:
            p[k] = v

    return p

def render_message(template_cfg: Dict[str, Any], key: str, payload: Dict[str, Any]) -> str:
    t = template_cfg.get(key) or template_cfg.get("default") or {}
    p = _prepare_payload(payload)

    # Use SafeDict to avoid KeyError; numeric/int defaults prevent format-spec failures.
    sd = _SafeDict(p)

    title = (t.get("title") or "").format_map(sd)
    lines = []
    for ln in (t.get("lines") or []):
        lines.append(ln.format_map(sd))
    return "\n".join([title] + lines).strip()
