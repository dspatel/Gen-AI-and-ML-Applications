from __future__ import annotations

import os
import csv
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

# =========================
# CONFIG
# =========================
SYMBOLS = ["SPY", "TSLA", "NVDA"]

INTERVAL = "5m"
PERIOD = "5d"      # for after-hours testing
PREPOST = False

DISPLAY_TZ = "America/Chicago"
SESSION_START_HM = (8, 30)
SESSION_END_HM = (15, 0)

ORB_MINUTES = 30
BAR_MINUTES = 5
ORB_BARS = ORB_MINUTES // BAR_MINUTES  # 6

# --- Strategy rules ---
RANGE_INCLUSIVE = True          # True => OR_low <= close <= OR_high is "back within range"
REARM_AFTER_REENTRY = True      # after a TRUE breakout, wait for re-entry before detecting another

# --- Run mode / testing ---
TEST_MODE = True
TEST_DATE = ""  # e.g. "2025-12-31" or "" auto-pick last trading day

# --- Logging ---
OUTPUT_DIR = Path("output")
LOG_FORMAT = "csv"  # currently "csv"

# --- Notifications toggles ---
ENABLE_NOTIFICATIONS = True

# Email (Outlook SMTP) optional
ENABLE_EMAIL = False
EMAIL_SUMMARY_PER_SYMBOL = True   # True => one email per symbol; False => one email per event

EMAIL_FROM = ""          # your outlook email
EMAIL_TO = ""            # destination email
EMAIL_APP_PASSWORD = ""  # Outlook app password recommended
SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587

# Discord optional
ENABLE_DISCORD = True
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1456378869618577522/haOyvDVoCds2LHI_QEtMEN_4Rf1idG091QL8ZKW7uXYknQeCY9kpWRUFJDkjql0joAB-"  # paste your Discord webhook URL

# --- Debug printing ---
PRINT_EVENTS = True  # prints event list in terminal


# =========================
# Utility helpers
# =========================
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _fmt_price(x: float) -> str:
    return f"{x:,.2f}"


def _fmt_int(x: int) -> str:
    return f"{x:,}"


def _direction_emoji(direction: str) -> str:
    return "📈" if "UP" in direction else "📉"


def _direction_word(direction: str) -> str:
    return "UP" if "UP" in direction else "DOWN"


def _embed_color_for_symbol(events: List["BreakoutEvent"]) -> int:
    """
    Pick a single color for the summary embed.
    - all UP => green
    - all DOWN => red
    - mixed => blue
    """
    if not events:
        return 0x3498DB  # blue
    ups = sum(1 for e in events if "UP" in e.direction)
    downs = sum(1 for e in events if "DOWN" in e.direction)
    if ups > 0 and downs == 0:
        return 0x2ECC71  # green
    if downs > 0 and ups == 0:
        return 0xE74C3C  # red
    return 0x3498DB  # mixed => blue


def _to_unix(ts: pd.Timestamp) -> int:
    # ts is timezone-aware
    return int(ts.timestamp())


def session_bounds(day_local_midnight: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    day = pd.Timestamp(day_local_midnight).tz_convert(ZoneInfo(DISPLAY_TZ)).normalize()
    start = day + pd.Timedelta(hours=SESSION_START_HM[0], minutes=SESSION_START_HM[1])
    end = day + pd.Timedelta(hours=SESSION_END_HM[0], minutes=SESSION_END_HM[1])
    return start, end


# =========================
# Data fetch
# =========================
def fetch_yahoo(symbol: str) -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        interval=INTERVAL,
        period=PERIOD,
        auto_adjust=False,
        prepost=PREPOST,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    # Handle MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1, drop_level=True)
        elif symbol in df.columns.get_level_values(0):
            df = df.xs(symbol, axis=1, level=0, drop_level=True)

    df.reset_index(inplace=True)

    if "Datetime" in df.columns:
        df.rename(columns={"Datetime": "time"}, inplace=True)
    elif "Date" in df.columns:
        df.rename(columns={"Date": "time"}, inplace=True)

    df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{symbol}: missing columns after normalization: {missing} | cols={list(df.columns)}")

    t = pd.to_datetime(df["time"])
    if t.dt.tz is None:
        t = t.dt.tz_localize("UTC")

    df["time_local"] = t.dt.tz_convert(ZoneInfo(DISPLAY_TZ))
    df = df.sort_values("time_local").reset_index(drop=True)
    return df[["time_local", "open", "high", "low", "close", "volume"]]


def choose_session_day(all_raw: dict[str, pd.DataFrame]) -> pd.Timestamp:
    tz = ZoneInfo(DISPLAY_TZ)

    if TEST_MODE and TEST_DATE.strip():
        return pd.Timestamp(TEST_DATE).tz_localize(tz).normalize()

    for pref in ["SPY", "TSLA", "NVDA"]:
        df = all_raw.get(pref, pd.DataFrame())
        if not df.empty:
            return df["time_local"].max().tz_convert(tz).normalize()

    raise RuntimeError("No data available to infer session day.")


def filter_session(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[(df["time_local"] >= start) & (df["time_local"] < end)].copy()
    return out.reset_index(drop=True)


def build_opening_range(session_df: pd.DataFrame) -> tuple[float, float]:
    if len(session_df) < ORB_BARS:
        raise RuntimeError(f"Need at least {ORB_BARS} bars to build opening range, got {len(session_df)}")
    orb_df = session_df.iloc[:ORB_BARS]
    return float(orb_df["high"].max()), float(orb_df["low"].min())


def is_within_range(close: float, or_high: float, or_low: float) -> bool:
    if RANGE_INCLUSIVE:
        return (or_low <= close <= or_high)
    return (or_low < close < or_high)


# =========================
# Event model + detection
# =========================
@dataclass
class BreakoutEvent:
    symbol: str
    session_date: str
    or_high: float
    or_low: float
    direction: str  # UP_TRUE / DOWN_TRUE

    breakout_dt: pd.Timestamp
    breakout_open: float
    breakout_high: float
    breakout_low: float
    breakout_close: float
    breakout_volume: int

    confirm_dt: pd.Timestamp
    confirm_open: float
    confirm_high: float
    confirm_low: float
    confirm_close: float
    confirm_volume: int

    @property
    def breakout_time(self) -> str:
        return str(self.breakout_dt)[:19]

    @property
    def confirm_time(self) -> str:
        return str(self.confirm_dt)[:19]


def detect_events(session_df: pd.DataFrame, symbol: str, session_date: str, or_high: float, or_low: float) -> list[BreakoutEvent]:
    """
    TRUE breakout + re-arm rule:
      - breakout candle i: close outside OR
      - confirm candle i+1: close continues in same direction (close[i+1] > close[i] for UP, < for DOWN)
      - after a true breakout, if REARM_AFTER_REENTRY=True, wait for a close back within OR to re-arm
    """
    events: list[BreakoutEvent] = []
    armed = True

    i = ORB_BARS
    while i < len(session_df) - 1:
        c0 = float(session_df.loc[i, "close"])

        # Disarmed => wait for re-entry
        if REARM_AFTER_REENTRY and not armed:
            if is_within_range(c0, or_high, or_low):
                armed = True
            i += 1
            continue

        c1 = float(session_df.loc[i + 1, "close"])

        direction = None
        if c0 > or_high and c1 > c0:
            direction = "UP_TRUE"
        elif c0 < or_low and c1 < c0:
            direction = "DOWN_TRUE"

        if direction is None:
            i += 1
            continue

        b = session_df.iloc[i]
        c = session_df.iloc[i + 1]

        ev = BreakoutEvent(
            symbol=symbol,
            session_date=session_date,
            or_high=or_high,
            or_low=or_low,
            direction=direction,

            breakout_dt=b["time_local"],
            breakout_open=float(b["open"]),
            breakout_high=float(b["high"]),
            breakout_low=float(b["low"]),
            breakout_close=float(b["close"]),
            breakout_volume=int(float(b["volume"])),

            confirm_dt=c["time_local"],
            confirm_open=float(c["open"]),
            confirm_high=float(c["high"]),
            confirm_low=float(c["low"]),
            confirm_close=float(c["close"]),
            confirm_volume=int(float(c["volume"])),
        )
        events.append(ev)

        if REARM_AFTER_REENTRY:
            armed = False

        i += 2  # skip past confirmation candle

    return events


# =========================
# Logging (CSV)
# =========================
def log_path_for_day(session_date: str) -> Path:
    mode = "TEST" if TEST_MODE else "LIVE"
    ensure_dir(OUTPUT_DIR / mode)
    return OUTPUT_DIR / mode / f"breakouts_{session_date}_{INTERVAL}.csv"


def append_events_to_csv(path: Path, events: list[BreakoutEvent]) -> None:
    if not events:
        return

    header = [
        "session_date", "symbol", "or_high", "or_low", "direction",
        "breakout_time", "breakout_open", "breakout_high", "breakout_low", "breakout_close", "breakout_volume",
        "confirm_time", "confirm_open", "confirm_high", "confirm_low", "confirm_close", "confirm_volume",
    ]

    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)

        for e in events:
            writer.writerow([
                e.session_date, e.symbol, f"{e.or_high:.4f}", f"{e.or_low:.4f}", e.direction,
                e.breakout_time, f"{e.breakout_open:.4f}", f"{e.breakout_high:.4f}", f"{e.breakout_low:.4f}",
                f"{e.breakout_close:.4f}", e.breakout_volume,
                e.confirm_time, f"{e.confirm_open:.4f}", f"{e.confirm_high:.4f}", f"{e.confirm_low:.4f}",
                f"{e.confirm_close:.4f}", e.confirm_volume,
            ])


# =========================
# Notifications - Email
# =========================
def send_email(subject: str, body: str) -> None:
    """
    Outlook SMTP. Recommended: use an App Password.
    """
    import smtplib
    from email.mime.text import MIMEText

    if not (EMAIL_FROM and EMAIL_TO and EMAIL_APP_PASSWORD):
        raise RuntimeError("Email is enabled but EMAIL_FROM/EMAIL_TO/EMAIL_APP_PASSWORD not set.")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())


def email_summary_for_symbol(symbol: str, session_date: str, or_high: float, or_low: float, events: list[BreakoutEvent]) -> None:
    if not events:
        return

    subject = f"{symbol} TRUE breakouts ({len(events)}) | {session_date} | ORH={_fmt_price(or_high)} ORL={_fmt_price(or_low)}"
    lines = [
        f"Symbol: {symbol}",
        f"Session: {session_date} (tz={DISPLAY_TZ})",
        f"ORH: {or_high:.2f}  ORL: {or_low:.2f}",
        "",
        "Events:"
    ]
    for i, e in enumerate(events, 1):
        lines.append(
            f"{i}) {e.direction} | breakout {e.breakout_time} close={e.breakout_close:.2f} vol={e.breakout_volume:,} "
            f"| confirm {e.confirm_time} close={e.confirm_close:.2f} vol={e.confirm_volume:,}"
        )

    send_email(subject=subject, body="\n".join(lines))


def email_per_event(e: BreakoutEvent) -> None:
    subject = f"{e.symbol} TRUE BREAKOUT {_direction_word(e.direction)} | ORH={_fmt_price(e.or_high)} ORL={_fmt_price(e.or_low)}"
    body = (
        f"{e.symbol} TRUE BREAKOUT ({e.direction})\n"
        f"Session: {e.session_date} (tz={DISPLAY_TZ})\n"
        f"ORH: {e.or_high:.2f} ORL: {e.or_low:.2f}\n\n"
        f"Breakout: {e.breakout_time} close={e.breakout_close:.2f} vol={e.breakout_volume:,}\n"
        f"Confirm : {e.confirm_time} close={e.confirm_close:.2f} vol={e.confirm_volume:,}\n"
    )
    send_email(subject=subject, body=body)

def _fmt_vol(v: int) -> str:
    # compact: 512,391 -> 512k ; 1,298,263 -> 1.30M
    v = int(v)
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}k"
    return str(v)

def _fmt_delta(x: float) -> str:
    # Always show sign
    return f"{x:+.2f}"

def _delta_from_or(direction: str, close: float, or_high: float, or_low: float) -> str:
    if "UP" in direction:
        return f"ΔORH {_fmt_delta(close - or_high)}"
    return f"ΔORL {_fmt_delta(close - or_low)}"


# =========================
# Notifications - Discord (one embed per symbol, relative time)
# =========================
def send_discord_symbol_summary(symbol: str, session_date: str, or_high: float, or_low: float, events: list[BreakoutEvent]) -> None:
    import requests

    if not DISCORD_WEBHOOK_URL.strip():
        raise RuntimeError("Discord is enabled but DISCORD_WEBHOOK_URL is empty.")

    up_events = [e for e in events if "UP" in e.direction]
    down_events = [e for e in events if "DOWN" in e.direction]

    totals_line = f"**Totals:** ⬆️ UP `{len(up_events)}`  •  ⬇️ DOWN `{len(down_events)}`"

    # Helpful quick context: last event time (if any)
    last_event = max(events, key=lambda e: e.confirm_dt) if events else None
    last_line = ""
    if last_event is not None:
        u = _to_unix(last_event.confirm_dt)
        last_line = f"\n**Last signal:** <t:{u}:t> (<t:{u}:R>)"

    embeds = []

    def build_embed(dir_word: str, arrow: str, color: int, dir_events: list[BreakoutEvent]) -> dict:
        title = f"{arrow} {symbol} — TRUE {dir_word} Breakouts ({len(dir_events)})"
        desc = (
            f"{totals_line}\n"
            f"**Session:** `{session_date}` • **TZ:** `{DISPLAY_TZ}`\n"
            f"**OR High:** `{_fmt_price(or_high)}` • **OR Low:** `{_fmt_price(or_low)}`\n"
            f"**Rules:** 2-candle confirm + re-arm after re-entry"
            f"{last_line}"
        )

        lines = []
        for e in dir_events:
            b_unix = _to_unix(e.breakout_dt)
            c_unix = _to_unix(e.confirm_dt)

            # Clean one-line format with volume
            delta_b = _delta_from_or(e.direction, e.breakout_close, or_high, or_low)
            delta_c = _delta_from_or(e.direction, e.confirm_close, or_high, or_low)

            lines.append(
                f"{arrow} "
                f"<t:{b_unix}:t> (<t:{b_unix}:R>) `C {_fmt_price(e.breakout_close)}` `V {_fmt_vol(e.breakout_volume)}` `{delta_b}` "
                f"→ "
                f"<t:{c_unix}:t> (<t:{c_unix}:R>) `C {_fmt_price(e.confirm_close)}` `V {_fmt_vol(e.confirm_volume)}` `{delta_c}`"
            )

        # Split into multiple fields if needed (field value max 1024 chars)
        fields = []
        chunk = ""
        part = 1
        for line in lines:
            if len(chunk) + len(line) + 1 > 950:
                fields.append({"name": f"Events (part {part})", "value": chunk, "inline": False})
                part += 1
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            fields.append({"name": "Events" if part == 1 else f"Events (part {part})", "value": chunk, "inline": False})

        return {
            "title": title,
            "description": desc,
            "color": color,
            "fields": fields,
            "footer": {"text": "ORB monitor • Yahoo 5m • per-symbol summary (split by direction)"},
        }

    # Direction embeds (only send ones that exist)
    if up_events:
        embeds.append(build_embed("UP", "⬆️", 0x2ECC71, up_events))
    if down_events:
        embeds.append(build_embed("DOWN", "⬇️", 0xE74C3C, down_events))

    if not embeds:
        return

    payload = {"embeds": embeds}
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord webhook failed: {resp.status_code} {resp.text}")
    

# =========================
# MAIN (single-run, testable after-hours)
# =========================
def main():
    clear_screen()
    print("STEP 6 | Yahoo ORB logging + Discord/email notifications (summary per symbol)")
    print(f"Symbols={SYMBOLS} Interval={INTERVAL} tz={DISPLAY_TZ} TEST_MODE={TEST_MODE} TEST_DATE={TEST_DATE or 'AUTO'}")
    print(f"Notifications: enabled={ENABLE_NOTIFICATIONS} discord={ENABLE_DISCORD} email={ENABLE_EMAIL}")
    print(f"Re-arm after re-entry: {REARM_AFTER_REENTRY} | Range inclusive: {RANGE_INCLUSIVE}")
    print("=" * 130)

    # Pull data
    raw_all = {sym: fetch_yahoo(sym) for sym in SYMBOLS}
    session_day = choose_session_day(raw_all)
    start, end = session_bounds(session_day)
    session_date_str = str(session_day.date())

    out_path = log_path_for_day(session_date_str)

    # Collect per-symbol results (for summary messages)
    per_symbol_events: Dict[str, List[BreakoutEvent]] = {}
    per_symbol_or: Dict[str, tuple[float, float]] = {}

    for sym in SYMBOLS:
        df = raw_all[sym]
        if df.empty:
            print(f"{sym}: NO DATA")
            per_symbol_events[sym] = []
            continue

        session_df = filter_session(df, start, end)
        if session_df.empty:
            print(f"{sym}: NO SESSION DATA")
            per_symbol_events[sym] = []
            continue

        or_high, or_low = build_opening_range(session_df)
        events = detect_events(session_df, sym, session_date_str, or_high, or_low)

        per_symbol_or[sym] = (or_high, or_low)
        per_symbol_events[sym] = events

        print(f"{sym}: ORH={or_high:.2f} ORL={or_low:.2f} | events={len(events)}")

        if PRINT_EVENTS and events:
            for i, e in enumerate(events, 1):
                print(f"  - {i}) {e.direction} breakout={e.breakout_time} confirm={e.confirm_time}")

    # Flatten for CSV logging
    all_events = [e for sym in SYMBOLS for e in per_symbol_events.get(sym, [])]
    append_events_to_csv(out_path, all_events)
    print(f"\nLogged {len(all_events)} event(s) to: {out_path}")

    # Notifications (summary per symbol)
    if ENABLE_NOTIFICATIONS:
        failures = 0

        for sym in SYMBOLS:
            events = per_symbol_events.get(sym, [])
            if not events:
                continue

            or_high, or_low = per_symbol_or[sym]

            # Discord summary (one message per symbol)
            if ENABLE_DISCORD:
                try:
                    send_discord_symbol_summary(sym, session_date_str, or_high, or_low, events)
                except Exception as ex:
                    failures += 1
                    print(f"DISCORD ERROR for {sym}: {ex}")

            # Email (summary or per-event)
            if ENABLE_EMAIL:
                try:
                    if EMAIL_SUMMARY_PER_SYMBOL:
                        email_summary_for_symbol(sym, session_date_str, or_high, or_low, events)
                    else:
                        for e in events:
                            email_per_event(e)
                except Exception as ex:
                    failures += 1
                    print(f"EMAIL ERROR for {sym}: {ex}")

        if failures == 0:
            print("Notifications sent successfully.")
        else:
            print(f"Notifications completed with {failures} failure(s).")

    print("\nDONE.")


if __name__ == "__main__":
    # pip install yfinance pandas requests
    main()
