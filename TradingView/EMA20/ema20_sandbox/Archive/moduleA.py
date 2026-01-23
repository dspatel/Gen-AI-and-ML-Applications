# ============================================================
# Module A: Yahoo Finance 30-day 5-minute Data Loader (SPY)
# Standalone script: run directly, no project integration needed
#
# What it does:
# - Downloads last 30 days of 5-minute OHLCV for SPY from Yahoo Finance
# - Cleans column names + types
# - Ensures timezone-aware timestamps (America/New_York)
# - Optional: filters to regular market hours (9:30–16:00 ET)
# - Runs sanity checks and saves a CSV snapshot for later modules
#
# Requirements:
#   pip install yfinance pandas
#
# Run:
#   python test_data_loader_spy.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

try:
    import yfinance as yf
except ImportError as e:
    raise SystemExit(
        "Missing dependency: yfinance.\n"
        "Install with: pip install yfinance pandas\n"
    ) from e


@dataclass(frozen=True)
class SessionConfig:
    timezone: str = "America/New_York"
    market_open: str = "09:30"
    market_close: str = "16:00"
    regular_hours_only: bool = True


def _standardize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Yahoo may return columns with different capitalization; standardize to:
    ['open','high','low','close','adj_close','volume'] (adj_close optional).
    """
    df = df.copy()

    # Flatten columns (yfinance sometimes returns multiindex)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    rename_map = {}
    for c in df.columns:
        cl = str(c).strip().lower().replace(" ", "_")
        if cl == "adj_close" or cl == "adjclose":
            rename_map[c] = "adj_close"
        elif cl in {"open", "high", "low", "close", "volume"}:
            rename_map[c] = cl
        else:
            # keep unknowns as-is but lower_snake_case them
            rename_map[c] = cl

    df = df.rename(columns=rename_map)

    # Keep only key columns if present
    keep = [c for c in ["open", "high", "low", "close", "adj_close", "volume"] if c in df.columns]
    df = df[keep]

    # Enforce numeric dtypes
    for c in ["open", "high", "low", "close", "adj_close"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")

    return df


def _ensure_datetime_index(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    """
    Ensures df.index is a timezone-aware DatetimeIndex in the requested tz.
    yfinance typically returns UTC timestamps for intraday; we normalize to tz.
    """
    df = df.copy()

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Expected a DatetimeIndex from yfinance.")

    # If index is naive, assume UTC (yfinance can be UTC-naive sometimes)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    # Convert to desired timezone
    df.index = df.index.tz_convert(tz)

    # Name the index for clarity
    df.index.name = "timestamp"
    return df


def _filter_regular_hours(df: pd.DataFrame, session: SessionConfig) -> pd.DataFrame:
    """
    Filters to regular market hours (9:30–16:00 ET). Keeps only Mon–Fri.
    """
    if not session.regular_hours_only:
        return df

    df = df.copy()
    # Weekdays only
    df = df[df.index.dayofweek < 5]

    # Between times uses local time of the index timezone
    df = df.between_time(session.market_open, session.market_close, inclusive="left")

    return df


def _basic_sanity_checks(df: pd.DataFrame, expected_interval_minutes: int = 5) -> Tuple[bool, list[str]]:
    """
    Runs lightweight checks to catch common issues early.
    Returns (ok, messages).
    """
    msgs = []
    ok = True

    if df.empty:
        return False, ["ERROR: DataFrame is empty. Yahoo may be rate-limiting or market data unavailable."]

    # Index sorted
    if not df.index.is_monotonic_increasing:
        ok = False
        msgs.append("ERROR: Index is not sorted ascending.")
    # Duplicates
    dupes = df.index.duplicated().sum()
    if dupes > 0:
        ok = False
        msgs.append(f"ERROR: Found {dupes} duplicate timestamps.")
    # Missing OHLC
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            ok = False
            msgs.append(f"ERROR: Missing required column '{col}'.")
        else:
            nan_count = df[col].isna().sum()
            if nan_count > 0:
                msgs.append(f"WARNING: {nan_count} NaNs in '{col}' (will matter later).")

    # Interval check (best-effort): look at median diff
    diffs = df.index.to_series().diff().dropna()
    if not diffs.empty:
        med = diffs.median()
        med_minutes = int(med.total_seconds() // 60)
        if med_minutes != expected_interval_minutes:
            msgs.append(
                f"WARNING: Median bar interval is ~{med_minutes} minutes (expected {expected_interval_minutes}). "
                "This can happen across session gaps; not always an error."
            )

    return ok, msgs


def fetch_yahoo_intraday(
    symbol: str,
    period: str = "30d",
    interval: str = "5m",
    session: Optional[SessionConfig] = None,
) -> pd.DataFrame:
    """
    Fetches intraday OHLCV from Yahoo using yfinance, cleans it, and returns a DataFrame.
    """
    session = session or SessionConfig()

    # yfinance download
    df = yf.download(
        tickers=symbol,
        period=period,
        interval=interval,
        auto_adjust=False,
        prepost=True,          # we'll filter regular hours ourselves if desired
        progress=False,
        threads=True,
    )

    if df is None or len(df) == 0:
        return pd.DataFrame()

    df = _standardize_ohlcv_columns(df)
    df = _ensure_datetime_index(df, session.timezone)
    df = _filter_regular_hours(df, session)

    # Drop rows with missing core OHLC (keep volume=0 ok)
    df = df.dropna(subset=[c for c in ["open", "high", "low", "close"] if c in df.columns])

    return df


def main():
    symbol = "SPY"
    session = SessionConfig(regular_hours_only=True)

    print(f"\n[Module A] Downloading {symbol} | period=30d | interval=5m | tz={session.timezone} ...")
    df = fetch_yahoo_intraday(symbol=symbol, period="30d", interval="5m", session=session)

    ok, msgs = _basic_sanity_checks(df, expected_interval_minutes=5)
    for m in msgs:
        print(m)

    if not ok:
        print("\nSanity checks failed. Fix data issues before moving on to Module B.")
        return

    print("\n✅ Data loaded successfully.")
    print(f"Rows: {len(df):,}")
    print(f"Start: {df.index.min()} | End: {df.index.max()}")
    print("Columns:", list(df.columns))

    print("\nSample (first 5 rows):")
    print(df.head(5))

    print("\nSample (last 5 rows):")
    print(df.tail(5))

    # Save snapshot
    out_dir = os.path.join(os.getcwd(), "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{symbol}_30d_5m_yahoo.csv")
    df.to_csv(out_path, index=True)

    print(f"\nSaved CSV snapshot to: {out_path}")
    print("\nNext: Module B (EMA20) will read this CSV or accept df directly.\n")


if __name__ == "__main__":
    main()
