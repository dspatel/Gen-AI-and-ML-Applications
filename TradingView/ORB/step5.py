from __future__ import annotations

import os
import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

# =========================
# CONFIG
# =========================
SYMBOLS = ["SPY", "TSLA", "NVDA"]

INTERVAL = "5m"
PERIOD = "5d"
PREPOST = False

DISPLAY_TZ = "America/Chicago"
SESSION_START_HM = (8, 30)
SESSION_END_HM = (15, 0)

ORB_MINUTES = 30
BAR_MINUTES = 5
ORB_BARS = ORB_MINUTES // BAR_MINUTES  # 6

# Testing
TEST_MODE = True
TEST_DATE = ""  # e.g. "2025-12-03" or "" auto pick last trading day

# Output / debug
SHOW_ORB_CANDLES = True
SHOW_CONTEXT_FOR_EACH_EVENT = True
CONTEXT_BARS_BEFORE = 5
CONTEXT_BARS_AFTER = 3

# Re-arm behavior
RANGE_INCLUSIVE = True  # True => OR_low <= close <= OR_high counts as "back within range"


# =========================
# Helpers
# =========================
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


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

    df.rename(columns={
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }, inplace=True)

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


def session_bounds(day_local_midnight: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    day = pd.Timestamp(day_local_midnight).tz_convert(ZoneInfo(DISPLAY_TZ)).normalize()
    start = day + pd.Timedelta(hours=SESSION_START_HM[0], minutes=SESSION_START_HM[1])
    end = day + pd.Timedelta(hours=SESSION_END_HM[0], minutes=SESSION_END_HM[1])
    return start, end


def filter_session(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[(df["time_local"] >= start) & (df["time_local"] < end)].copy()
    return out.reset_index(drop=True)


def build_opening_range(session_df: pd.DataFrame) -> tuple[float, float, pd.DataFrame]:
    if len(session_df) < ORB_BARS:
        raise RuntimeError(f"Need at least {ORB_BARS} bars to build opening range, got {len(session_df)}")

    orb_df = session_df.iloc[:ORB_BARS].copy()
    or_high = float(orb_df["high"].max())
    or_low = float(orb_df["low"].min())
    return or_high, or_low, orb_df


def is_within_range(close: float, or_high: float, or_low: float) -> bool:
    if RANGE_INCLUSIVE:
        return (or_low <= close <= or_high)
    return (or_low < close < or_high)


def find_all_true_breakouts_rearm(session_df: pd.DataFrame, or_high: float, or_low: float) -> pd.DataFrame:
    """
    Finds TRUE breakout events with a re-arm rule:
      - After a TRUE breakout is detected, we ignore further breakouts
        until we see a candle close back WITHIN the OR range.
      - Once price is within range again, we re-arm and look for the next TRUE breakout.

    TRUE breakout rule (2-candle):
      breakout candle i:
        UP   if close[i] > or_high
        DOWN if close[i] < or_low
      confirm candle i+1 must continue:
        UP   if close[i+1] > close[i]
        DOWN if close[i+1] < close[i]
    """
    events = []
    armed = True  # can detect breakouts
    last_breakout_direction = None

    # need i+1
    i = ORB_BARS
    while i < len(session_df) - 1:
        c0 = float(session_df.loc[i, "close"])

        # If we're disarmed, wait for a close back within range
        if not armed:
            if is_within_range(c0, or_high, or_low):
                armed = True
                last_breakout_direction = None
            i += 1
            continue

        # Armed: check for breakout at i and confirm at i+1
        c1 = float(session_df.loc[i + 1, "close"])

        direction = None
        if c0 > or_high and c1 > c0:
            direction = "UP_TRUE"
        elif c0 < or_low and c1 < c0:
            direction = "DOWN_TRUE"

        if direction is None:
            i += 1
            continue

        # Record event
        b = session_df.iloc[i]
        c = session_df.iloc[i + 1]
        events.append({
            "breakout_index": i,
            "direction": direction,
            "or_high": or_high,
            "or_low": or_low,

            "breakout_time": b["time_local"],
            "breakout_open": float(b["open"]),
            "breakout_high": float(b["high"]),
            "breakout_low": float(b["low"]),
            "breakout_close": float(b["close"]),
            "breakout_volume": int(float(b["volume"])),

            "confirm_time": c["time_local"],
            "confirm_open": float(c["open"]),
            "confirm_high": float(c["high"]),
            "confirm_low": float(c["low"]),
            "confirm_close": float(c["close"]),
            "confirm_volume": int(float(c["volume"])),
        })

        # Disarm after a true breakout; we must see a close back within range to re-arm
        armed = False
        last_breakout_direction = direction

        # Skip ahead by 2 bars so we don't immediately re-process the confirmation candle as a breakout candle
        i += 2

    return pd.DataFrame(events)


def print_context(session_df: pd.DataFrame, i: int):
    start_i = max(0, i - CONTEXT_BARS_BEFORE)
    end_i = min(len(session_df), (i + 2) + CONTEXT_BARS_AFTER)  # includes confirm candle i+1
    ctx = session_df.iloc[start_i:end_i].copy()
    ctx["time_local"] = ctx["time_local"].astype(str).str.slice(0, 19)
    print(ctx[["time_local", "open", "high", "low", "close", "volume"]].to_string(index=False))


# =========================
# MAIN
# =========================
def main():
    raw_all = {sym: fetch_yahoo(sym) for sym in SYMBOLS}
    session_day = choose_session_day(raw_all)
    start, end = session_bounds(session_day)

    clear_screen()
    print(f"STEP 5 | TRUE Breakouts with Re-arm (single OR for the day) | {session_day.date()} | tz={DISPLAY_TZ}")
    print(f"Session: {start} → {end}")
    print("Rule: breakout candle closes outside OR; next candle continues (close confirm).")
    print("Re-arm: after a TRUE breakout, wait until a candle CLOSES back within OR range before detecting another breakout.")
    print("NOTE: We are NOT redefining the range.")
    print("=" * 150)

    for sym in SYMBOLS:
        print(f"\n>>> {sym}")
        df = raw_all[sym]
        if df.empty:
            print("NO DATA")
            continue

        session_df = filter_session(df, start, end)
        if session_df.empty:
            print("NO SESSION DATA")
            continue

        or_high, or_low, orb_df = build_opening_range(session_df)

        if SHOW_ORB_CANDLES:
            orb_view = orb_df[["time_local", "high", "low", "close"]].copy()
            orb_view["time_local"] = orb_view["time_local"].astype(str).str.slice(0, 19)
            print("Opening Range candles (first 30m):")
            print(orb_view.to_string(index=False))

        print(f"\nOR HIGH: {or_high:.2f} | OR LOW: {or_low:.2f}")

        events = find_all_true_breakouts_rearm(session_df, or_high, or_low)
        if events.empty:
            print("\nNo TRUE breakouts for this session (with re-arm rule).")
            continue

        print(f"\n✅ TRUE breakouts found (with re-arm): {len(events)}")
        for k, row in events.iterrows():
            bt = str(row["breakout_time"])[:19]
            ct = str(row["confirm_time"])[:19]
            print(
                f"\nEvent {k+1}: {row['direction']} | "
                f"Breakout @ {bt} close={row['breakout_close']:.2f} | "
                f"Confirm @ {ct} close={row['confirm_close']:.2f}"
            )

            if SHOW_CONTEXT_FOR_EACH_EVENT:
                print("\nContext window:")
                print_context(session_df, int(row["breakout_index"]))

    print("\nDONE.")


if __name__ == "__main__":
    # pip install yfinance pandas
    main()
