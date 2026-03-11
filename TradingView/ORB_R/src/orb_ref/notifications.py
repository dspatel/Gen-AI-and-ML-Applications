
from __future__ import annotations

from typing import Dict, Any
import yaml

def load_templates(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def render_message(template_cfg: Dict[str, Any], key: str, payload: Dict[str, Any]) -> str:
    t = template_cfg.get(key) or template_cfg.get("default") or {}
    title = (t.get("title") or "").format(**payload)
    lines = []
    for ln in (t.get("lines") or []):
        lines.append(ln.format(**payload))
    return "\n".join([title] + lines).strip()
