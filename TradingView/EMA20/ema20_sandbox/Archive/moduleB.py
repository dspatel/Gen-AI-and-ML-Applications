# ============================================================
# Module B: EMA20 Indicator Engine (5-minute bars)
# Standalone script for SPY (reads Module A CSV)
#
# Input:
#   data/SPY_30d_5m_yahoo.csv
#
# Output:
#   data/SPY_30d_5m_yahoo_ema20.csv
#
# What it adds:
#   - ema20
#   - ema20_prev
#   - ema_slope (ema20 - ema20_prev)
#   - ema_slope_lookback (ema20 - ema20.shift(lookback))
#   - ema_slope_perc (percent change over lookback)
#   - price_vs_ema ('ABOVE'/'BELOW'/'AT')
#
# Run:
#   python test_ema20_spy.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EMAConfig:
    length: int = 20
    lookback_bars: int = 5              # for conservative slope filter
    at_threshold_perc: float = 0.01     # within 0.01% considered "AT" EMA


def load_ohlcv_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Input CSV not found: {path}\n"
            "Make sure Module A created it under ./data/"
        )

    df = pd.read_csv(path)

    # Module A saves timestamp as index column named "timestamp"
    if "timestamp" not in df.columns:
        raise ValueError("CSV must contain a 'timestamp' column.")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")

    # If timestamps were saved with timezone offset already, utc=True is safe;
    # convert to America/New_York for consistent market-time logic.
    df["timestamp"] = df["timestamp"].dt.tz_convert("America/New_York")

    df = df.set_index("timestamp").sort_index()

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Ensure numeric
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def compute_ema20(df: pd.DataFrame, cfg: EMAConfig) -> pd.DataFrame:
    out = df.copy()

    # Exponential Moving Average
    out["ema20"] = out["close"].ewm(span=cfg.length, adjust=False).mean()

    out["ema20_prev"] = out["ema20"].shift(1)
    out["ema_slope"] = out["ema20"] - out["ema20_prev"]

    # Lookback slope (used for conservative trend filter)
    lb = cfg.lookback_bars
    out["ema_slope_lookback"] = out["ema20"] - out["ema20"].shift(lb)

    # Percent slope over lookback (avoid division by 0)
    denom = out["ema20"].shift(lb).replace(0, pd.NA)
    out["ema_slope_perc"] = (out["ema_slope_lookback"] / denom) * 100.0  # percent

    # Price vs EMA status
    # "AT" if within cfg.at_threshold_perc of ema
    dist_perc = (out["close"] - out["ema20"]) / out["ema20"] * 100.0
    out["dist_from_ema_perc"] = dist_perc

    thr = cfg.at_threshold_perc
    out["price_vs_ema"] = "AT"
    out.loc[dist_perc > thr, "price_vs_ema"] = "ABOVE"
    out.loc[dist_perc < -thr, "price_vs_ema"] = "BELOW"

    return out


def sanity_checks(df: pd.DataFrame, cfg: EMAConfig) -> tuple[bool, list[str]]:
    msgs = []
    ok = True

    if df.empty:
        return False, ["ERROR: DataFrame is empty after loading/cleaning."]

    if df.index.tz is None:
        ok = False
        msgs.append("ERROR: Index is not timezone-aware.")

    # EMA should exist
    if "ema20" not in df.columns:
        return False, ["ERROR: ema20 column missing (computation failed)."]

    # EMA NaNs should be minimal (typically none with ewm, but confirm)
    ema_nans = df["ema20"].isna().sum()
    if ema_nans > 0:
        msgs.append(f"WARNING: ema20 has {ema_nans} NaNs. Unexpected for ewm().")

    # Lookback slope will have NaNs in first lookback bars (expected)
    lb_nans = df["ema_slope_lookback"].isna().sum()
    expected_min = cfg.lookback_bars
    if lb_nans < expected_min:
        msgs.append(
            "WARNING: ema_slope_lookback NaNs fewer than expected; verify indexing."
        )

    # Quick value checks
    if (df["ema20"] <= 0).any():
        ok = False
        msgs.append("ERROR: Found non-positive ema20 values (should not happen for SPY).")

    # Check that close exists and is positive
    if (df["close"] <= 0).any():
        ok = False
        msgs.append("ERROR: Found non-positive close values.")

    return ok, msgs


def main():
    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20.csv")

    cfg = EMAConfig(length=20, lookback_bars=5, at_threshold_perc=0.01)

    print("\n[Module B] Loading CSV from Module A:")
    print(f"  {in_path}")

    df = load_ohlcv_csv(in_path)
    print(f"Loaded rows: {len(df):,}")
    print(f"Start: {df.index.min()} | End: {df.index.max()}")
    print("Columns:", list(df.columns))

    print("\nComputing EMA20 + slope metrics...")
    out = compute_ema20(df, cfg)

    ok, msgs = sanity_checks(out, cfg)
    for m in msgs:
        print(m)
    if not ok:
        print("\nSanity checks failed. Fix issues before moving on to Module C (ORB).")
        return

    # Show a small sample
    sample_cols = [
        "close", "ema20", "ema20_prev", "ema_slope",
        "ema_slope_lookback", "ema_slope_perc", "dist_from_ema_perc", "price_vs_ema"
    ]
    print("\nSample (last 10 rows):")
    print(out[sample_cols].tail(10).round(4))

    # Save enriched CSV
    out.to_csv(out_path, index=True)
    print(f"\n✅ Saved enriched EMA CSV to: {out_path}")

    print("\nNext: Module C (ORB engine) will use ORH/ORL + EMA fields for signals.\n")


if __name__ == "__main__":
    main()
