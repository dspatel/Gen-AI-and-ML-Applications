from __future__ import annotations

import sys
from typing import Tuple

import pandas as pd
from tvDatafeed import TvDatafeed, Interval
from zoneinfo import ZoneInfo

# ============ CONFIG ============
SYMBOLS = [
    {"symbol": "SPY", "exchange": "AMEX"},
    {"symbol": "TSLA", "exchange": "NASDAQ"},
    {"symbol": "NVDA", "exchange": "NASDAQ"},
]
INTERVAL = Interval.in_5_minute
N_BARS = 800
DISPLAY_TZ = "America/Chicago"

# Keyring
KEYRING_SERVICE = "tradingview"
KEYRING_USER_KEY = "username"
KEYRING_PASS_KEY = "password"


def read_keyring_creds() -> Tuple[str, str]:
    import keyring  # type: ignore
    u = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_KEY)
    p = keyring.get_password(KEYRING_SERVICE, KEYRING_PASS_KEY)
    if not u or not p:
        raise RuntimeError("Missing keyring creds for TradingView.")
    return u, p


def to_local(ts: pd.Timestamp) -> pd.Timestamp:
    # Treat tz-naive timestamps as already aligned to DISPLAY_TZ (no UTC assumption)
    t = pd.Timestamp(ts)
    tz = ZoneInfo(DISPLAY_TZ)
    if t.tzinfo is None:
        return t.tz_localize(tz)
    return t.tz_convert(tz)


def normalize_hist_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if df.index.name in ("datetime", "time") and "time" not in df.columns:
        df.reset_index(inplace=True)
    if "datetime" in df.columns and "time" not in df.columns:
        df.rename(columns={"datetime": "time"}, inplace=True)

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing columns: {missing}")

    df["time_local"] = df["time"].apply(to_local)
    df = df.sort_values("time_local").reset_index(drop=True)
    return df


def diagnose_volume(df: pd.DataFrame) -> str:
    """
    Checks whether volume looks cumulative or per-candle WITHOUT converting anything.
    """
    if df.empty:
        return "NO DATA"

    v = df["volume"].astype(float)

    diffs = v.diff().dropna()
    n = len(diffs)
    if n == 0:
        return "Not enough rows to diagnose volume."

    # How often does it decrease?
    dec = int((diffs < 0).sum())
    inc = int((diffs > 0).sum())
    zero = int((diffs == 0).sum())

    dec_pct = 100.0 * dec / n
    inc_pct = 100.0 * inc / n
    zero_pct = 100.0 * zero / n

    # Look for "session reset" behavior: big negative drop (common for cumulative reset)
    # threshold: drop bigger than 50% of previous value
    prev = v.shift(1)
    big_drop = ((diffs < 0) & (v < 0.5 * prev)).sum()

    # Heuristic classification
    if dec == 0 and inc_pct > 70:
        cls = "LIKELY CUMULATIVE (monotonic non-decreasing)"
    elif dec_pct < 2 and big_drop > 0:
        cls = "LIKELY CUMULATIVE WITH RESETS (big negative drops observed)"
    elif dec_pct > 10:
        cls = "LIKELY PER-CANDLE (frequent decreases)"
    else:
        cls = "UNCLEAR (mixed behavior)"

    # Show last 20 volumes to eyeball
    tail_vols = v.tail(20).tolist()
    tail_times = df["time_local"].tail(20).astype(str).str.slice(0, 19).tolist()

    # show some negative diff examples if any
    neg_examples = ""
    if dec > 0:
        neg_idx = diffs[diffs < 0].index[:5]
        rows = []
        for i in neg_idx:
            rows.append(
                f"{df.loc[i,'time_local']} vol={v.loc[i]:.0f}  prev={v.loc[i-1]:.0f}  diff={diffs.loc[i]:.0f}"
            )
        neg_examples = "\nNegative diff examples:\n  " + "\n  ".join(rows)

    return (
        f"{cls}\n"
        f"Diff stats: n={n}  inc={inc} ({inc_pct:.1f}%)  dec={dec} ({dec_pct:.1f}%)  zero={zero} ({zero_pct:.1f}%)\n"
        f"Big reset-like drops (vol < 50% prev): {int(big_drop)}\n"
        f"Last 20 bars (time, volume):\n"
        + "\n".join([f"  {t}  {vv:.0f}" for t, vv in zip(tail_times, tail_vols)])
        + neg_examples
    )


def main():
    u, p = read_keyring_creds()
    tv = TvDatafeed(username=u, password=p)

    # Smoke test
    test = tv.get_hist("SPY", "AMEX", INTERVAL, n_bars=5)
    test = normalize_hist_df(test)
    if test.empty:
        raise RuntimeError("Login smoke test failed (no data).")

    print("✅ Login smoke test passed.\n")

    for s in SYMBOLS:
        sym, exch = s["symbol"], s["exchange"]
        raw = tv.get_hist(sym, exch, INTERVAL, n_bars=N_BARS)
        df = normalize_hist_df(raw)

        print(f"\n==================== {sym} ({exch}) ====================")
        if df.empty:
            print("NO DATA RETURNED")
            continue

        print(f"Rows: {len(df)}")
        print(f"First: {df.iloc[0]['time_local']}")
        print(f"Last : {df.iloc[-1]['time_local']}")
        print(diagnose_volume(df))

    print("\nDONE.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n❌ ERROR:", e)
        sys.exit(1)
