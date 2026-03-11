from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import json

# 'requests' is already in the dependency tree (yfinance -> requests).
import requests


@dataclass
class DiscordConfig:
    enabled: bool = False
    webhook_url: str = ""
    username: Optional[str] = None
    timeout_sec: int = 15
    debug: bool = True


class DiscordNotifier:
    def __init__(self, cfg: DiscordConfig):
        self.cfg = cfg

    def send(self, title: str, message: str) -> Tuple[int, str]:
        """Send a Discord webhook message.

        Returns: (status_code, response_text)
        Never raises on HTTP errors; logs debug info when enabled.
        """
        if not self.cfg.enabled or not self.cfg.webhook_url:
            return (0, "")

        url = (self.cfg.webhook_url or "").strip()
        payload = {"content": f"**{title}**\n{message}"}
        if self.cfg.username:
            payload["username"] = self.cfg.username

        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.cfg.timeout_sec,
                headers={"Content-Type": "application/json"},
            )
            # Discord returns 204 No Content on success sometimes.
            text = resp.text or ""
            if self.cfg.debug and resp.status_code >= 400:
                # Helpful details: many 403s come from a proxy, revoked webhook, or invalid URL.
                print(f"[discord] HTTP {resp.status_code}: {resp.reason} :: {text[:500]}")
                print(f"[discord] webhook host: {requests.utils.urlparse(url).netloc}")
            return (resp.status_code, text)
        except requests.exceptions.RequestException as e:
            if self.cfg.debug:
                print(f"[discord] request error: {e}")
                try:
                    print(f"[discord] webhook host: {requests.utils.urlparse(url).netloc}")
                except Exception:
                    pass
            return (0, str(e))


def build_notifier(cfg: dict) -> DiscordNotifier:
    ncfg = cfg.get("notifications", {}) or {}
    discord = ncfg.get("discord", {}) or {}
    dc = DiscordConfig(
        enabled=bool(ncfg.get("enabled", False)) and bool(discord.get("webhook_url")),
        webhook_url=str(discord.get("webhook_url", "")),
        username=discord.get("username"),
        timeout_sec=int(discord.get("timeout_sec", 15) or 15),
        debug=bool(discord.get("debug", True)),
    )
    return DiscordNotifier(dc)
