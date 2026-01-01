from __future__ import annotations

import os
import math
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

# Opening range length
ORB_MINUTES = 30
CANDLES_PER_ORB = ORB_MINUTES // 5  # 6 candles

TEST_MODE = True
TEST_DATE = ""   # e.g. "2025-12-03" or "" = auto pick last trading day


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

    # Flatten MultiIndex if needed
    if isinstance(df.columns, pd.MultiIndex):
        df = df.xs(symbol, axis=1, level=-1, drop_level=True)

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
        "Volume": "volume"
    }, inplace=True)

    t = pd.to_datetime(df["time"])
    if t.dt.tz is None:
        t = t.dt.tz_localize("UTC")

    df["time_local"] = t.dt.tz_convert(ZoneInfo(DISPLAY_TZ))
    df = df.sort_values("time_local").reset_index(drop=True)

    return df[["time_local", "open", "high", "low", "close", "volume"]]


def choose_session_day(all_raw: dict[str, pd.DataFrame]) -> pd.Timestamp:
    tz = ZoneInfo(DISPLAY_TZ)

    if TEST_MODE and TEST_DATE:
        return pd.Timestamp(TEST_DATE).tz_localize(tz).normalize()

    for sym in SYMBOLS:
        df = all_raw.get(sym)
        if df is not None and not df.empty:
            return df["time_local"].max().normalize()

    raise RuntimeError("No data available to infer session date.")


def session_bounds(day: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = day + pd.Timedelta(hours=SESSION_START_HM[0], minutes=SESSION_START_HM[1])
    end = day + pd.Timedelta(hours=SESSION_END_HM[0], minutes=SESSION_END_HM[1])
    return start, end


def filter_session(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["time_local"] >= start) & (df["time_local"] < end)].reset_index(drop=True)


def build_opening_range(df: pd.DataFrame) -> tuple[float, float, pd.DataFrame]:
    if len(df) < CANDLES_PER_ORB:
        raise RuntimeError("Not enough candles to build opening range")

    orb_df = df.iloc[:CANDLES_PER_ORB].copy()
    orb_high = float(orb_df["high"].max())
    orb_low = float(orb_df["low"].min())
    return orb_high, orb_low, orb_df


# =========================
# MAIN
# =========================
def main():
    raw = {}
    for sym in SYMBOLS:
        raw[sym] = fetch_yahoo(sym)

    session_day = choose_session_day(raw)
    start, end = session_bounds(session_day)

    clear_screen()
    print(f"STEP 3 | Opening Range (30m) | {session_day.date()} | tz={DISPLAY_TZ}")
    print(f"Session: {start} → {end}")
    print("=" * 100)

    for sym, df in raw.items():
        print(f"\n>>> {sym}")

        if df.empty:
            print("NO DATA")
            continue

        session_df = filter_session(df, start, end)

        if session_df.empty:
            print("NO SESSION DATA")
            continue

        orb_high, orb_low, orb_df = build_opening_range(session_df)

        print("\nOpening Range Candles (first 30 minutes):")
        view = orb_df[["time_local", "high", "low"]].copy()
        view["time_local"] = view["time_local"].astype(str).str.slice(0, 19)
        print(view.to_string(index=False))

        print(f"\n➡️ OR HIGH: {orb_high:.2f}")
        print(f"➡️ OR LOW : {orb_low:.2f}")

    print("\nDONE.")


if __name__ == "__main__":
    main()
