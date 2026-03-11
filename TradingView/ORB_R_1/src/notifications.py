from __future__ import annotations

from typing import Dict, Any
import yaml

class _SafeDict(dict):
    """dict that returns 'N/A' for missing keys during str.format_map"""
    def __missing__(self, key: str) -> str:
        return "N/A"

def load_templates(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def render_message(template_cfg: Dict[str, Any], key: str, payload: Dict[str, Any]) -> str:
    t = template_cfg.get(key) or template_cfg.get("default") or {}
    safe = _SafeDict(payload or {})
    title = (t.get("title") or "").format_map(safe)
    lines = []
    for ln in (t.get("lines") or []):
        lines.append(str(ln).format_map(safe))
    return "\n".join([title] + lines).strip()
