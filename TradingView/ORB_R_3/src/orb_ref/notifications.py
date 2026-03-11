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


def _derive_fields(payload: dict) -> dict:
    """Derive human-friendly fields used by notification templates."""
    p = dict(payload)

    bt = str(p.get("breakout_time_local") or p.get("breakout_time") or p.get("timestamp") or "")
    p["breakout_time_local"] = bt

    dec = p.get("decision", "")
    conf = p.get("confidence_pct")
    conf_txt = f"{int(conf)}%" if isinstance(conf, (int, float)) else ""
    reasons = p.get("decision_reasons", "")
    dash = " — " if reasons else ""
    p["decision_line"] = f"Decision: {dec} ({conf_txt}){dash}{reasons}".strip()

    story = p.get("story", "") or p.get("market_story", "")
    p["story_line"] = story if story else "—"

    tag_line = p.get("tag_line")
    if not tag_line:
        bits = []
        for k in ["open_alignment", "reference_shape", "regime", "direction_bias", "breakout_strength"]:
            v = p.get(k)
            if v:
                bits.append(str(v))
        tag_line = " | ".join(bits) if bits else "—"
    p["tag_line"] = tag_line

    ref_high = p.get("ref_high")
    ref_low = p.get("ref_low")
    width = p.get("ref_width")
    wtxt = f" (W={float(width):.2f})" if isinstance(width, (int, float)) else ""
    if isinstance(ref_high, (int, float)) and isinstance(ref_low, (int, float)):
        p["ref_line"] = f"{float(ref_low):.2f}–{float(ref_high):.2f}{wtxt}"
    else:
        p["ref_line"] = "—"

    oor = p.get("or_overlap_ratio")
    p["or_overlap_ratio_pct"] = float(oor) * 100.0 if isinstance(oor, (int, float)) else 0.0

    pop = p.get("pair_overlap_pct")
    if isinstance(pop, (int, float)):
        p["pair_overlap_pct"] = float(pop)
    else:
        pop01 = p.get("pair_overlap_ratio")
        p["pair_overlap_pct"] = float(pop01) * 100.0 if isinstance(pop01, (int, float)) else 0.0

    for k in ["close_pen_pct", "wick_pen_pct", "body_pct", "range_pct"]:
        v = p.get(k)
        p[k] = float(v) if isinstance(v, (int, float)) else 0.0

    lad = p.get("ladder", "")
    if not lad:
        first = p.get("ladder_first")
        broke = p.get("ladder_broken") or []
        notb = p.get("ladder_not_broken") or []
        sim = p.get("ladder_simultaneous") or []
        parts = []
        if first:
            parts.append(f"first {first}")
        if broke:
            parts.append("broke " + ",".join(map(str, broke)))
        if sim:
            parts.append("sim " + ",".join(map(str, sim)))
        if notb:
            parts.append("not " + ",".join(map(str, notb)))
        lad = " | ".join(parts) if parts else "—"
    p["ladder_line"] = lad

    phase = p.get("phase_label") or p.get("phase") or ""
    p["phase_label"] = phase if phase else "—"

    try:
        p["horizon"] = int(p.get("horizon"))
    except Exception:
        pass

    return p



def render_title(template_cfg: Dict[str, Any], key: str, payload: Dict[str, Any]) -> str:
    """Render the one-line title for a notification.

    We keep title rendering separate so we can avoid duplicating the title inside
    the message body (Discord already shows a prominent first line).
    """
    t = template_cfg.get(key) or template_cfg.get("default") or {}
    p = _prepare_payload(payload)
    sd = _SafeDict(p)
    return (t.get("title") or "").format_map(sd).strip()


def render_message(template_cfg: Dict[str, Any], key: str, payload: Dict[str, Any]) -> str:
    """Render the body of a notification (no title line)."""
    t = template_cfg.get(key) or template_cfg.get("default") or {}
    p = _prepare_payload(payload)
    sd = _SafeDict(p)

    lines: list[str] = []
    for ln in (t.get("lines") or []):
        s = str(ln).format_map(sd)
        if s.strip():
            lines.append(s)
    return "\n".join(lines).strip()
