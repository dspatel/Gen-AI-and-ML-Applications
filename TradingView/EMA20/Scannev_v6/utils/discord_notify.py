import json
from typing import Dict, Any, Optional, List

import requests


def _post_json(webhook_url: str, payload: Dict[str, Any], timeout: int = 10) -> None:
    """Post a JSON payload to a Discord webhook.

    Uses `requests` and raises RuntimeError with response body on failure.
    """
    if not webhook_url:
        return
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ema20-scanner/1.0",
    }
    r = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=timeout)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord HTTPError {r.status_code}: {r.text}")


def send_discord_message(webhook_url: str, content: str) -> None:
    if not webhook_url:
        return
    _post_json(webhook_url, {"content": content})


def _fnum(x: Any, ndp: int = 2) -> str:
    try:
        return f"{float(x):.{ndp}f}"
    except Exception:
        return str(x)

"""Human readable alert message.

    Expects alert fields from the live tracker / EOD scanner. Handles both:
    - dynamic window label keys (WindowHigh_35D_preCross)
    - or generic primary/secondary fields (WindowDaysPrimary, WindowHighPrimary, etc.)
    """
def _fmt(x, nd=2):
    if x is None:
        return "NA"
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)

def _pct(x, nd=1):
    if x is None:
        return "NA"
    try:
        return f"{float(x)*100:.{nd}f}%"
    except Exception:
        return str(x)

def format_alert_message(alert: dict, env: str = "PROD") -> str:
    signal = alert.get("Signal", "NA")
    symbol = alert.get("Symbol", "NA")

    # Emoji + title
    emoji = "🟢" if signal.upper() == "LONG" else "🔴" if signal.upper() == "SHORT" else "⚪"
    title = f"{emoji} EMA20 Anchored Breakout ({env}) — {signal.upper()}"

    # Times
    event_date = alert.get("EventDate", "NA")
    candle_time = alert.get("CandleTime", "NA")
    event_time = alert.get("EventTime", "NA")

    # Price/EMA
    px = _fmt(alert.get("TodayClose"))
    ema20 = _fmt(alert.get("EMA20"))
    ema20_h = _fmt(alert.get("EMA20_H"))
    ema20_l = _fmt(alert.get("EMA20_L"))

    # Windows (dynamic)
    p_days = alert.get("PrimaryWindowDaysUsed")
    s_days = alert.get("SecondaryWindowDaysUsed")

    def win_line(days):
        if not days:
            return None
        hi = alert.get(f"WindowHigh_{int(days)}D_preCross")
        lo = alert.get(f"WindowLow_{int(days)}D_preCross")
        return f"• {int(days)}D: High {_fmt(hi)} | Low {_fmt(lo)}"

    p_line = win_line(p_days)
    s_line = win_line(s_days)

    # Metrics (dynamic)
    p_break = alert.get(f"BreakPct_{int(p_days)}D") if p_days else None
    s_break = alert.get(f"BreakPct_{int(s_days)}D") if s_days else None
    ema_dist = _fmt(alert.get("EmaDistance"))

    # Cross context (once)
    cross_date = alert.get("LatestCrossDate", "NA")
    cross_dir = alert.get("LatestCrossDirection", "NA")

    lines = [
        f"**{title}**",
        "",
        f"**Symbol:** {symbol}",
        f"**Event Date:** {event_date}",
        f"**Candle:** {candle_time}   |   **Fired:** {event_time}",
        "",
        f"**Price:** {px}",
        f"**EMA20:** {ema20}   (H: {ema20_h}  L: {ema20_l})",
        "",
        "**Anchored Windows (pre-cross)**",
    ]

    if p_line:
        lines.append(p_line)
    if s_line:
        lines.append(s_line)

    lines += [
        "",
        "**Break Metrics**",
    ]

    if p_days:
        lines.append(f"• Break % ({int(p_days)}D): {_pct(p_break)}")
    if s_days:
        lines.append(f"• Break % ({int(s_days)}D): {_pct(s_break)}")

    lines += [
        f"• EMA Distance: {ema_dist}",
        "",
        f"**Last EMA20 Cross:** {cross_date} ({cross_dir})",
    ]

    return "\n".join(lines)


def format_eod_summary(run_date: str, universe: int, eligible: int, alerts_count: int, longs: int, shorts: int) -> str:
    lines = []
    lines.append("📊 EMA20 Scanner — End of Day Summary")
    lines.append(f"Date: {run_date}")
    lines.append("")
    lines.append(f"Universe: {universe}")
    lines.append(f"Eligible (cross in lookback): {eligible}")
    lines.append("")
    lines.append(f"Alerts: {alerts_count} (LONG: {longs} | SHORT: {shorts})")
    return "\n".join(lines)


def format_alerts_table(alerts_df, max_rows: int = 15) -> str:
    """Compact table-like text for Discord (kept short)."""
    try:
        import pandas as pd  # local import to avoid hard dependency for message formatting
        if alerts_df is None or len(alerts_df) == 0:
            return "No alerts."
        df = alerts_df.copy()
        cols = [c for c in ["Symbol", "Signal", "CandleTime", "EventTime", "EMA20", "EMA20_H", "EMA20_L"] if c in df.columns]
        df = df[cols].head(max_rows)
        # Build monospace table-ish lines
        lines = ["```"]
        lines.append(" | ".join(cols))
        lines.append("-" * min(120, max(10, len(lines[-1]))))
        for _, r in df.iterrows():
            lines.append(" | ".join(str(r.get(c, "")) for c in cols))
        lines.append("```")
        if len(alerts_df) > max_rows:
            lines.append(f"(showing {max_rows} of {len(alerts_df)})")
        return "\n".join(lines)
    except Exception:
        return "Alerts ready (table rendering failed)."
