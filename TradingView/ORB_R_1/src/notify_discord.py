from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
import json

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None


@dataclass(frozen=True)
class DiscordConfig:
    enabled: bool
    webhook_url: str
    replay_tag: bool = True
    timeout_s: int = 10


def discord_config_from_cfg(cfg: Dict[str, Any]) -> DiscordConfig:
    n = cfg.get("notifications", {}) or {}
    return DiscordConfig(
        enabled=bool(n.get("enabled", False)),
        webhook_url=str(n.get("discord_webhook_url", "") or "").strip(),
        replay_tag=bool(n.get("replay_tag", True)),
        timeout_s=int(n.get("timeout_s", 10)),
    )


def send_discord_message(dc: DiscordConfig, content: str) -> None:
    """Send a simple Discord webhook message. No-op if disabled or webhook missing."""
    if not dc.enabled:
        return
    if not dc.webhook_url:
        return
    if requests is None:
        raise RuntimeError("requests is not installed; add it to requirements.txt")

    payload = {"content": content}
    r = requests.post(dc.webhook_url, json=payload, timeout=dc.timeout_s)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord webhook failed: {r.status_code} {r.text[:200]}")
