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

TEST_MODE = True
TEST_DATE = ""  # e.g. "2025-12-03" or "" = auto pick last trading day

DEBUG_CONTEXT_BARS = 10  # show bars around breakout


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

    # auto pick last available day (prefer SPY)
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


def find_first_true_breakout(session_df: pd.DataFrame, or_high: float, or_low: float) -> tuple[int, str] | None:
    """
    Two-step breakout confirmation:

    Step A: breakout candle i:
      UP   if close[i] > or_high
      DOWN if close[i] < or_low

    Step B: confirm candle i+1:
      UP   is true only if close[i+1] > close[i]
      DOWN is true only if close[i+1] < close[i]

    Returns (breakout_index_i, direction) for the first TRUE breakout.
    """
    # Need at least one candle after breakout candle to confirm
    for i in range(ORB_BARS, len(session_df) - 1):
        c0 = float(session_df.loc[i, "close"])
        c1 = float(session_df.loc[i + 1, "close"])

        # Potential UP breakout
        if c0 > or_high:
            if c1 > c0:
                return i, "UP_TRUE"
            else:
                # Not confirmed -> ignore
                continue

        # Potential DOWN breakout
        if c0 < or_low:
            if c1 < c0:
                return i, "DOWN_TRUE"
            else:
                # Not confirmed -> ignore
                continue

    return None


def format_row(row: pd.Series) -> str:
    t = str(row["time_local"])[:19]
    return (
        f"{t}  O={float(row['open']):.2f} H={float(row['high']):.2f} "
        f"L={float(row['low']):.2f} C={float(row['close']):.2f} V={int(float(row['volume']))}"
    )


# =========================
# MAIN
# =========================
def main():
    raw_all = {sym: fetch_yahoo(sym) for sym in SYMBOLS}
    session_day = choose_session_day(raw_all)
    start, end = session_bounds(session_day)

    clear_screen()
    print(f"STEP 4 | TRUE Breakout Confirmation (2-candle) | {session_day.date()} | tz={DISPLAY_TZ}")
    print(f"Session: {start} → {end}")
    print("NOTE: We are NOT redefining the range in this step. One OR range for the day.")
    print("=" * 130)

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

        print(f"Opening Range (first {ORB_MINUTES}m / {ORB_BARS} bars):")
        orb_view = orb_df[["time_local", "high", "low", "close"]].copy()
        orb_view["time_local"] = orb_view["time_local"].astype(str).str.slice(0, 19)
        print(orb_view.to_string(index=False))

        print(f"\nOR HIGH: {or_high:.2f} | OR LOW: {or_low:.2f}")

        res = find_first_true_breakout(session_df, or_high, or_low)
        if res is None:
            print("\nNo TRUE breakout found (based on 2-candle confirmation rule).")
            continue

        i, direction = res
        br = session_df.iloc[i]
        confirm = session_df.iloc[i + 1]

        print(f"\n✅ FIRST TRUE BREAKOUT: {direction}")
        print("\nBreakout candle (i):")
        print("  " + format_row(br))
        print("\nConfirmation candle (i+1):")
        print("  " + format_row(confirm))

        # Context around breakout
        start_i = max(0, i - (DEBUG_CONTEXT_BARS // 2))
        end_i = min(len(session_df), i + 2 + (DEBUG_CONTEXT_BARS // 2))
        ctx = session_df.iloc[start_i:end_i].copy()
        ctx["time_local"] = ctx["time_local"].astype(str).str.slice(0, 19)

        print(f"\nContext (bars around breakout):")
        print(ctx[["time_local", "open", "high", "low", "close", "volume"]].to_string(index=False))

    print("\nDONE.")


if __name__ == "__main__":
    # pip install yfinance pandas
    main()
