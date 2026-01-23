import json
import requests
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List

def _post_json(webhook_url: str, payload: Dict[str, Any], timeout: int = 10) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if e.fp else ""
        raise RuntimeError(f"Discord HTTPError {e.code}: {e.reason}. Body: {body}") from e

def send_discord_message(webhook_url: str, content: str, timeout: int = 10):
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is empty.")

    payload = {"content": content}

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ema20-scanner/1.0 (+https://example.local)"
    }

    r = requests.post(webhook_url, data=json.dumps(payload), headers=headers, timeout=timeout)

    # Discord often returns 204 on success
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Discord HTTPError {r.status_code}: {r.text}")

def format_alert_message(alert: Dict[str, Any], env: str = "TEST") -> str:
    # Keep message readable and consistent
    lines = []
    lines.append(f"🚨 EMA20 Anchored Breakout ({env})")
    lines.append("")
    lines.append(f"Symbol: {alert.get('Symbol')}")
    lines.append(f"Signal: {alert.get('Signal')}")
    lines.append(f"Event Date: {alert.get('EventDate')}")
    if alert.get("CandleTime"):
        lines.append(f"Candle Time: {alert.get('CandleTime')}")
    if alert.get("EventTime"):
        lines.append(f"Event Time: {alert.get('EventTime')}")
    lines.append("")
    def fnum(x):
        try:
            return f"{float(x):.2f}"
        except Exception:
            return str(x)
    lines.append(f"Price/Close: {fnum(alert.get('TodayClose'))}")
    lines.append(f"EMA20: {fnum(alert.get('EMA20'))} | EMA20_H: {fnum(alert.get('EMA20_H'))} | EMA20_L: {fnum(alert.get('EMA20_L'))}")
    lines.append("")
    lines.append(f"7D Window High/Low: {fnum(alert.get('WindowHigh_7D_preCross'))} / {fnum(alert.get('WindowLow_7D_preCross'))}")
    lines.append(f"21D Window High/Low: {fnum(alert.get('WindowHigh_21D_preCross'))} / {fnum(alert.get('WindowLow_21D_preCross'))}")
    lines.append("")
    lines.append(f"Break % of 7D Range: {fnum(alert.get('BreakPctOfRange_7D'))}")
    lines.append(f"Break % of 21D Range: {fnum(alert.get('BreakPctOfRange_21D'))}")
    lines.append(f"EMA Distance: {fnum(alert.get('EmaDistance'))}")
    lines.append("")
    lines.append(f"Latest Cross: {alert.get('LatestCrossDate')} ({alert.get('LatestCrossDirection')})")
    return "\n".join(lines)

def format_eod_summary(run_date: str, universe: int, eligible: int, alerts_count: int, longs: int, shorts: int, env: str="TEST") -> str:
    return (
        f"📊 EMA20 Scanner Summary ({env})\n\n"
        f"Date: {run_date}\n"
        f"Universe: {universe}\n"
        f"Cross-Eligible (last 30d): {eligible}\n"
        f"Alerts: {alerts_count} (LONG {longs} | SHORT {shorts})"
    )

def format_alerts_table(alerts: List[Dict[str, Any]], max_rows: int = 10) -> str:
    if not alerts:
        return "No alerts."
    # Simple fixed-width table
    rows = alerts[:max_rows]
    header = f"{'Date':10}  {'Sym':6}  {'Sig':5}  {'Close':>9}  {'EMA20':>9}  {'7DHigh':>9}  {'7DLow':>9}"
    lines = [header, "-"*len(header)]
    for a in rows:
        def fnum(x):
            try: return f"{float(x):.2f}"
            except Exception: return str(x)
        lines.append(f"{a.get('EventDate','')[:10]:10}  {a.get('Symbol','')[:6]:6}  {a.get('Signal','')[:5]:5}  {fnum(a.get('TodayClose')):>9}  {fnum(a.get('EMA20')):>9}  {fnum(a.get('WindowHigh_7D_preCross')):>9}  {fnum(a.get('WindowLow_7D_preCross')):>9}")
    return "```\n" + "\n".join(lines) + "\n```"
