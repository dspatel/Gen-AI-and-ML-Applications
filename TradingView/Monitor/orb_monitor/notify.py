from __future__ import annotations

import ssl
import pandas as pd
from typing import Dict

from .config import Config
from .strategy import BreakoutEvent, SymbolState

def _fmt_price(x: float) -> str:
    return f"{x:,.2f}"

def _fmt_vol(v: int) -> str:
    v = int(v)
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}k"
    return str(v)

def _fmt_delta(x: float) -> str:
    return f"{x:+.2f}"

def _delta_from_or(direction: str, close: float, or_high: float, or_low: float) -> str:
    if "UP" in direction:
        return f"ΔORH {_fmt_delta(close - or_high)}"
    return f"ΔORL {_fmt_delta(close - or_low)}"

def _to_unix(ts: pd.Timestamp) -> int:
    return int(ts.timestamp())

def _arrow(direction: str) -> str:
    return "⬆️" if "UP" in direction else "⬇️"

def _color(direction: str) -> int:
    return 0x2ECC71 if "UP" in direction else 0xE74C3C

def send_discord_embeds(cfg: Config, embeds: list[dict]) -> None:
    import requests
    if not cfg.discord_webhook_url.strip():
        raise RuntimeError("Discord webhook URL not set (cfg.discord_webhook_url).")
    resp = requests.post(cfg.discord_webhook_url, json={"embeds": embeds}, timeout=15)
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook failed: {resp.status_code} {resp.text}")

def notify_event_discord(cfg: Config, e: BreakoutEvent) -> None:
    """Immediate per-event notification (one embed per event)."""
    arrow = _arrow(e.direction)
    color = _color(e.direction)

    b_unix = _to_unix(e.breakout_dt)
    c_unix = _to_unix(e.confirm_dt)
    delta_b = _delta_from_or(e.direction, e.breakout_close, e.or_high, e.or_low)
    delta_c = _delta_from_or(e.direction, e.confirm_close, e.or_high, e.or_low)

    title = f"{arrow} {e.symbol} TRUE {('UP' if 'UP' in e.direction else 'DOWN')} Breakout"
    if e.is_catchup:
        title += " (catchup)"

    line = (
        f"{arrow} "
        f"<t:{b_unix}:t> (<t:{b_unix}:R>) `C {_fmt_price(e.breakout_close)}` `V {_fmt_vol(e.breakout_volume)}` `{delta_b}` "
        f"→ "
        f"<t:{c_unix}:t> (<t:{c_unix}:R>) `C {_fmt_price(e.confirm_close)}` `V {_fmt_vol(e.confirm_volume)}` `{delta_c}`"
    )

    embed = {
        "title": title,
        "description": (
            f"**Session:** `{e.session_date}` • **TZ:** `{cfg.tz}`\n"
            f"**OR High:** `{_fmt_price(e.or_high)}` • **OR Low:** `{_fmt_price(e.or_low)}`\n"
            f"**Rules:** 2-candle confirm + re-arm after re-entry"
        ),
        "color": color,
        "fields": [{"name": "Event", "value": line, "inline": False}],
        "footer": {"text": "ORB monitor • LIVE • Yahoo"},
    }

    send_discord_embeds(cfg, [embed])

def notify_market_close_summary_discord(cfg: Config, states: Dict[str, SymbolState], session_date: str) -> None:
    """One daily close summary message."""
    lines = []
    for sym, st in states.items():
        up = sum(1 for e in st.events if "UP" in e.direction)
        dn = sum(1 for e in st.events if "DOWN" in e.direction)
        orh = f"{st.or_high:.2f}" if st.or_high is not None else "---"
        orl = f"{st.or_low:.2f}" if st.or_low is not None else "---"
        lines.append(f"**{sym}** ORH `{orh}` ORL `{orl}` • ⬆️ `{up}` ⬇️ `{dn}` • total `{len(st.events)}`")

    embed = {
        "title": f"🔔 Market Close Summary — {session_date}",
        "description": f"**TZ:** `{cfg.tz}`\n\n" + "\n".join(lines),
        "color": 0x3498DB,
        "footer": {"text": "ORB monitor • daily summary"},
    }
    send_discord_embeds(cfg, [embed])

# Optional Outlook email sender (disabled by default)
def send_email(cfg: Config, subject: str, body: str) -> None:
    import smtplib
    from email.mime.text import MIMEText

    if not (cfg.email_from and cfg.email_to and cfg.email_app_password):
        raise RuntimeError("Email enabled but credentials not set in config.")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = cfg.email_from
    msg["To"] = cfg.email_to

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
        server.starttls(context=context)
        server.login(cfg.email_from, cfg.email_app_password)
        server.sendmail(cfg.email_from, [cfg.email_to], msg.as_string())
