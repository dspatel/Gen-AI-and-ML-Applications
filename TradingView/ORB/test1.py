from __future__ import annotations

import sys
from typing import Tuple, List

import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from zoneinfo import ZoneInfo


# =========================
# CONFIG (edit here)
# =========================
SYMBOLS = [
    {"symbol": "SPY", "exchange": "AMEX"},     # if empty, try "NYSEARCA"
    {"symbol": "TSLA", "exchange": "NASDAQ"},  # if empty, try fallback exchanges
    {"symbol": "NVDA", "exchange": "NASDAQ"},
]

INTERVAL = Interval.in_5_minute
N_BARS = 800  # enough to cover today's session + buffer

DISPLAY_TZ = "America/Chicago"

# Keyring entries
KEYRING_SERVICE = "tradingview"
KEYRING_USER_KEY = "username"
KEYRING_PASS_KEY = "password"


# =========================
# Helpers
# =========================
def to_display_tz(ts: pd.Timestamp) -> pd.Timestamp:
    """
    tvDatafeed timestamps are often tz-naive.
    We localize naive timestamps directly to DISPLAY_TZ (do NOT assume UTC),
    which matched your working verification behavior.
    """
    t = pd.Timestamp(ts)
    tz = ZoneInfo(DISPLAY_TZ)
    if t.tzinfo is None:
        return t.tz_localize(tz)
    return t.tz_convert(tz)


def normalize_intraday_volume(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert cumulative intraday volume to per-candle volume if needed.

    Some TradingView feeds return cumulative session volume for intraday candles.
    This function detects monotonic non-decreasing volume and diffs it.

    - Adds 'volume_raw' when conversion is applied
    - Ensures volume is non-negative
    """
    if df.empty or "volume" not in df.columns:
        return df

    df = df.copy()

    # Detect "likely cumulative volume": volume never decreases
    diffs = df["volume"].diff()
    is_monotonic_non_decreasing = diffs.fillna(0).ge(0).all()

    if is_monotonic_non_decreasing:
        df["volume_raw"] = df["volume"]
        df["volume"] = df["volume"].diff().fillna(df["volume"])
        df["volume"] = df["volume"].clip(lower=0)

    return df


def normalize_hist_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure df has: time, open, high, low, close, volume
    Add time_local in DISPLAY_TZ
    Normalize volume to per-candle if cumulative
    """
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    # tvDatafeed often returns datetime index
    if df.index.name in ("datetime", "time") and "time" not in df.columns:
        df.reset_index(inplace=True)

    # Sometimes index column is named "datetime"
    if "datetime" in df.columns and "time" not in df.columns:
        df.rename(columns={"datetime": "time"}, inplace=True)

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing columns from tvDatafeed output: {missing}. Got: {list(df.columns)}")

    df["time_local"] = df["time"].apply(to_display_tz)
    df = df.sort_values("time_local").reset_index(drop=True)

    # Convert cumulative -> per-candle volume if needed
    #df = normalize_intraday_volume(df)

    return df


def read_keyring_creds() -> Tuple[str, str]:
    try:
        import keyring  # type: ignore
    except Exception as e:
        raise RuntimeError("keyring not installed. Run: pip install keyring") from e

    u = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_KEY)
    p = keyring.get_password(KEYRING_SERVICE, KEYRING_PASS_KEY)

    if not u or not p:
        raise RuntimeError(
            f"Missing keyring creds. Expected:\n"
            f"  service='{KEYRING_SERVICE}', username key='{KEYRING_USER_KEY}', password key='{KEYRING_PASS_KEY}'\n"
            f"Use your setup_keyring.py to store them."
        )
    return u, p


def connect_tv_login() -> TvDatafeed:
    """
    Enforce login mode.
    If TradingView blocks programmatic login, tvDatafeed may print:
      'error while signin'
    This script validates usability with a smoke-test that must return data.
    """
    u, p = read_keyring_creds()
    tv = TvDatafeed(username=u, password=p)

    # Smoke test: must fetch a few bars (and normalize them)
    test = tv.get_hist(symbol="SPY", exchange="AMEX", interval=INTERVAL, n_bars=5)
    test = normalize_hist_df(test)
    if test.empty:
        raise RuntimeError(
            "Login smoke test returned EMPTY data.\n"
            "Most likely TradingView blocked sign-in (captcha/2FA) or exchange code is wrong.\n"
            "Try SPY exchange='NYSEARCA' and re-run."
        )
    return tv


def render_symbol_block(sym: str, exch: str, df: pd.DataFrame) -> str:
    lines: List[str] = []
    lines.append(f"\n=== {sym} ({exch}) ===")

    if df.empty:
        lines.append("NO DATA RETURNED.")
        return "\n".join(lines)

    first = df.iloc[0]
    last = df.iloc[-1]

    lines.append(f"rows: {len(df)}")
    lines.append(f"first bar: {first['time_local']}")
    lines.append(f"last  bar: {last['time_local']}")

    # Volume output: show per-candle volume, plus raw if we converted
    vol_note = ""
    if "volume_raw" in df.columns:
        vol_note = " (per-candle; converted from cumulative 'volume_raw')"
    lines.append(
        f"last OHLCV: O={last['open']:.2f} H={last['high']:.2f} L={last['low']:.2f} "
        f"C={last['close']:.2f} V={float(last['volume']):.0f}{vol_note}"
    )

    # last 10 candles
    cols = ["time_local", "open", "high", "low", "close", "volume"]
    #view = df[cols].tail(10).copy()
    view = df[cols].copy()
    view["time_local"] = view["time_local"].astype(str).str.slice(0, 19)
    lines.append("\nLast 10 candles (volume shown is per-candle):")
    lines.append(view.to_string(index=False))

    # quick sanity check on volume monotonicity
    v_last = df["volume"].tail(10).tolist()
    lines.append(f"\nLast 10 per-candle volumes: {[int(x) for x in v_last]}")

    return "\n".join(lines)


def main():
    print("Connecting to TradingView via tvDatafeed (LOGIN MODE)...")
    tv = connect_tv_login()
    print("✅ Login smoke test passed.\n")

    for s in SYMBOLS:
        sym = s["symbol"]
        exch = s["exchange"]

        raw = tv.get_hist(symbol=sym, exchange=exch, interval=INTERVAL, n_bars=N_BARS)
        df = normalize_hist_df(raw)

        print(render_symbol_block(sym, exch, df))

    print("\nDONE.")
    print("If any symbol shows NO DATA, we’ll fix exchange mapping before moving to Step 2.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n❌ ERROR:", str(e))
        sys.exit(1)
