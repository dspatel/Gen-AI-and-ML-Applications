from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import yaml
import requests


def load_templates(path: str) -> Dict[str, str]:
    """Load YAML templates.

    Supports either:
    - {template: "..."}
    - {templates: {breakout_default: "...", ...}}
    Returns a flat dict: {name: template_string}
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict) and "templates" in raw and isinstance(raw["templates"], dict):
        return {str(k): str(v) for k, v in raw["templates"].items()}
    if isinstance(raw, dict) and "template" in raw:
        return {"template": str(raw["template"])}
    return {}


def render_alert(templates: Dict[str, str], payload: Dict[str, Any], template_key: str = "breakout_default") -> str:
    tmpl = templates.get(template_key) or templates.get("template") or "{symbol} {direction}"
    return tmpl.format(**payload)


def send_discord(webhook_url: str, content: str) -> bool:
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=10)
        return 200 <= r.status_code < 300
    except Exception:
        return False
