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
PERIOD = "5d"       # so yesterday/last trading day is available
PREPOST = False     # regular session only

DISPLAY_TZ = "America/Chicago"

SESSION_START_HM = (8, 30)  # 8:30 CT
SESSION_END_HM = (15, 0)    # 3:00 CT

SHOW_LAST_N = 15

TEST_MODE = True
TEST_DATE = ""  # e.g. "2025-12-03" or "" to auto-pick last trading day


# =========================
# Helpers
# =========================
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def now_local() -> pd.Timestamp:
    return pd.Timestamp.now(tz=ZoneInfo(DISPLAY_TZ))


def parse_test_date() -> pd.Timestamp | None:
    if not TEST_MODE:
        return None
    if not TEST_DATE.strip():
        return None
    tz = ZoneInfo(DISPLAY_TZ)
    return pd.Timestamp(TEST_DATE).tz_localize(tz).normalize()


def choose_effective_session_date(all_raw: dict[str, pd.DataFrame]) -> pd.Timestamp:
    tz = ZoneInfo(DISPLAY_TZ)

    explicit = parse_test_date()
    if explicit is not None:
        return explicit

    if TEST_MODE:
        for pref in ["SPY", "TSLA", "NVDA"]:
            df = all_raw.get(pref, pd.DataFrame())
            if not df.empty:
                last_ts = df["time_local"].max()
                return pd.Timestamp(last_ts).tz_convert(tz).normalize()
        return now_local().normalize()

    return now_local().normalize()


def session_bounds_for_day(day_local_midnight: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    tz = ZoneInfo(DISPLAY_TZ)
    day = pd.Timestamp(day_local_midnight).tz_convert(tz).normalize()
    start = day + pd.Timedelta(hours=SESSION_START_HM[0], minutes=SESSION_START_HM[1])
    end = day + pd.Timedelta(hours=SESSION_END_HM[0], minutes=SESSION_END_HM[1])
    return start, end


def expected_candles_for_session(interval_minutes: int, session_start: pd.Timestamp, session_end: pd.Timestamp) -> int:
    minutes = (session_end - session_start).total_seconds() / 60.0
    return int(math.floor(minutes / interval_minutes))


def _flatten_yf_columns(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    yfinance sometimes returns MultiIndex columns (field, ticker) or (ticker, field).
    This function flattens them and keeps only the target symbol when needed.
    """
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    # Try (Field, Ticker)
    if symbol in df.columns.get_level_values(-1):
        df = df.xs(symbol, axis=1, level=-1, drop_level=True)
        return df

    # Try (Ticker, Field)
    if symbol in df.columns.get_level_values(0):
        df = df.xs(symbol, axis=1, level=0, drop_level=True)
        return df

    # Fallback: just flatten names
    df.columns = ["_".join([str(x) for x in tup if x is not None]).strip() for tup in df.columns.to_list()]
    return df


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

    # Fix MultiIndex columns
    df = _flatten_yf_columns(df, symbol)

    # Time index to column
    df.reset_index(inplace=True)

    # Normalize time column name
    if "Datetime" in df.columns:
        df.rename(columns={"Datetime": "time"}, inplace=True)
    elif "Date" in df.columns:
        df.rename(columns={"Date": "time"}, inplace=True)

    # Normalize OHLCV names (yfinance uses Title case)
    rename_map = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{symbol}: missing columns after normalization: {missing}. Columns={list(df.columns)}")

    t = pd.to_datetime(df["time"])
    if t.dt.tz is None:
        t = t.dt.tz_localize("UTC")
    df["time_local"] = t.dt.tz_convert(ZoneInfo(DISPLAY_TZ))

    df = df.sort_values("time_local").reset_index(drop=True)
    return df[["time_local", "open", "high", "low", "close", "volume"]]


def filter_session_for_day(df: pd.DataFrame, session_start: pd.Timestamp, session_end: pd.Timestamp) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[(df["time_local"] >= session_start) & (df["time_local"] < session_end)].copy()
    return out.reset_index(drop=True)


def render_dashboard(all_session: dict[str, pd.DataFrame], session_start: pd.Timestamp, session_end: pd.Timestamp) -> str:
    exp = expected_candles_for_session(5, session_start, session_end)

    lines = []
    lines.append(f"STEP 2 | Yahoo {INTERVAL} Session Filter | tz={DISPLAY_TZ}")
    lines.append(f"TEST_MODE={TEST_MODE}  TEST_DATE='{TEST_DATE or 'AUTO'}'")
    lines.append(f"Selected session: {session_start}  →  {session_end}")
    lines.append("-" * 150)
    lines.append(
        f"{'SYM':<6} {'BARS':>5} {'EXP':>5} {'FIRST_BAR':<19} {'LAST_BAR':<19} "
        f"{'CLOSE':>10} {'HIGH':>10} {'LOW':>10} {'VOL':>12}"
    )
    lines.append("-" * 150)

    for sym, df in all_session.items():
        if df.empty:
            lines.append(f"{sym:<6} {0:>5} {exp:>5} {'':<19} {'':<19} {'':>10} {'':>10} {'':>10} {'':>12}")
            continue

        first = df.iloc[0]
        last = df.iloc[-1]

        first_t = str(first["time_local"])[:19]
        last_t = str(last["time_local"])[:19]

        close = float(last["close"])
        high = float(last["high"])
        low = float(last["low"])
        vol = int(float(last["volume"]))

        lines.append(
            f"{sym:<6} {len(df):>5} {exp:>5} {first_t:<19} {last_t:<19} "
            f"{close:>10.2f} {high:>10.2f} {low:>10.2f} {vol:>12}"
        )

    lines.append("\n" + "=" * 150)
    lines.append(f"LAST {SHOW_LAST_N} SESSION BARS (time_local, high, low, close, volume)")
    lines.append("=" * 150)

    for sym, df in all_session.items():
        lines.append(f"\n>>> {sym}")
        if df.empty:
            lines.append("NO SESSION DATA (check TEST_DATE or Yahoo availability)")
            continue

        view = df[["time_local", "high", "low", "close", "volume"]].tail(SHOW_LAST_N).copy()
        view["time_local"] = view["time_local"].astype(str).str.slice(0, 19)
        lines.append(view.to_string(index=False))

    return "\n".join(lines)


def main():
    raw_all: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        raw_all[sym] = fetch_yahoo(sym)

    session_day = choose_effective_session_date(raw_all)
    session_start, session_end = session_bounds_for_day(session_day)

    session_all: dict[str, pd.DataFrame] = {}
    for sym, df in raw_all.items():
        session_all[sym] = filter_session_for_day(df, session_start, session_end)

    clear_screen()
    print(render_dashboard(session_all, session_start, session_end))


if __name__ == "__main__":
    # pip install yfinance pandas
    main()
