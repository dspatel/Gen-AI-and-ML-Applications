import json
from typing import Dict, Any, Optional, List

import requests
import os
import uuid
import urllib.request


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


def format_alert_message(alert: Dict[str, Any], env: str = "LIVE") -> str:
    """Human readable alert message.

    Expects alert fields from the live tracker / EOD scanner. Handles both:
    - dynamic window label keys (WindowHigh_35D_preCross)
    - or generic primary/secondary fields (WindowDaysPrimary, WindowHighPrimary, etc.)
    """
    sym = alert.get("Symbol") or alert.get("symbol")
    signal = alert.get("Signal") or alert.get("signal")

    lines: List[str] = []
    lines.append(f"🚨 EMA20 Anchored Breakout ({env})")
    lines.append("")
    lines.append(f"Symbol: {sym}")
    lines.append(f"Signal: {signal}")

    event_date = alert.get("EventDate") or alert.get("event_date")
    if event_date:
        lines.append(f"Event Date: {event_date}")

    candle_time = alert.get("CandleTime") or alert.get("candle_time")
    if candle_time:
        lines.append(f"Candle Time: {candle_time}")

    event_time = alert.get("EventTime") or alert.get("event_time")
    if event_time:
        lines.append(f"Alert Sent: {event_time}")

    lines.append("")
    price = alert.get("TodayClose") or alert.get("close")
    ema20 = alert.get("EMA20") or alert.get("ema20")
    ema20_h = alert.get("EMA20_H") or alert.get("ema20_h")
    ema20_l = alert.get("EMA20_L") or alert.get("ema20_l")

    lines.append(f"Price: {_fnum(price)}")
    lines.append(f"EMA20: {_fnum(ema20)} | EMA20_H: {_fnum(ema20_h)} | EMA20_L: {_fnum(ema20_l)}")
    lines.append("")

    # Window lengths
    d1 = alert.get("WindowDaysPrimary") or alert.get("window_days_primary") or alert.get("WindowDays_Primary")
    d2 = alert.get("WindowDaysSecondary") or alert.get("window_days_secondary") or alert.get("WindowDays_Secondary")

    # Primary window values
    wh1 = alert.get("WindowHighPrimary") or alert.get("window_high_primary") or alert.get("WindowHigh_Primary_preCross")
    wl1 = alert.get("WindowLowPrimary") or alert.get("window_low_primary") or alert.get("WindowLow_Primary_preCross")
    bp1 = alert.get("BreakPctPrimary") or alert.get("break_pct_primary") or alert.get("BreakPct_Primary")

    # If dynamic keys exist, prefer them
    if d1:
        try:
            d1_int = int(d1)
            wh1 = wh1 if wh1 is not None else alert.get(f"WindowHigh_{d1_int}D_preCross")
            wl1 = wl1 if wl1 is not None else alert.get(f"WindowLow_{d1_int}D_preCross")
            bp1 = bp1 if bp1 is not None else alert.get(f"BreakPct_{d1_int}D")
        except Exception:
            pass

    if d1:
        lines.append(f"{int(d1)}D Window High/Low: {_fnum(wh1)} / {_fnum(wl1)}")
        if bp1 is not None:
            lines.append(f"Break % of {int(d1)}D Range: {_fnum(bp1)}")
        lines.append("")

    # Secondary window (optional)
    if d2:
        wh2 = alert.get("WindowHighSecondary") or alert.get("window_high_secondary") or alert.get("WindowHigh_Secondary_preCross")
        wl2 = alert.get("WindowLowSecondary") or alert.get("window_low_secondary") or alert.get("WindowLow_Secondary_preCross")
        bp2 = alert.get("BreakPctSecondary") or alert.get("break_pct_secondary") or alert.get("BreakPct_Secondary")

        try:
            d2_int = int(d2)
            wh2 = wh2 if wh2 is not None else alert.get(f"WindowHigh_{d2_int}D_preCross")
            wl2 = wl2 if wl2 is not None else alert.get(f"WindowLow_{d2_int}D_preCross")
            bp2 = bp2 if bp2 is not None else alert.get(f"BreakPct_{d2_int}D")
        except Exception:
            pass

        lines.append(f"{int(d2)}D Window High/Low: {_fnum(wh2)} / {_fnum(wl2)}")
        if bp2 is not None:
            lines.append(f"Break % of {int(d2)}D Range: {_fnum(bp2)}")
        lines.append("")

    ema_dist = alert.get("EmaDistance") or alert.get("ema_dist") or alert.get("EmaDist")
    if ema_dist is not None:
        lines.append(f"EMA Distance: {_fnum(ema_dist)}")
        lines.append("")

    cross_date = alert.get("LatestCrossDate") or alert.get("cross_date") or alert.get("LatestCrossDate")
    cross_dir = alert.get("LatestCrossDirection") or alert.get("cross_direction") or alert.get("LatestCrossDirection")
    if cross_date or cross_dir:
        lines.append(f"Latest Cross: {cross_date} ({cross_dir})")

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
