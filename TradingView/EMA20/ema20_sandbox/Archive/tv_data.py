"""
Download SPY 5-minute candles for the last ~6 months from TradingView using tvDatafeed,
then save to disk.

Install:
    pip install tvDatafeed pandas pyarrow

Notes:
- TradingView often limits how many bars you can fetch per call.
- This script fetches in chunks going backwards, then concatenates, dedupes, and saves.
- You may need TradingView credentials for reliable access (free accounts can still work).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from tvDatafeed import TvDatafeed, Interval


# ----------------------------
# CONFIG
# ----------------------------
SYMBOL = "SPY"
EXCHANGE = "AMEX"  # TradingView commonly uses AMEX for SPY
INTERVAL = Interval.in_5_minute

# How far back
MONTHS_BACK = 6

# Chunk size per request (TradingView/tvDatafeed limits vary; 3000-8000 is typical)
CHUNK_BARS = 5000

# Safety max loops (prevents infinite loops if API repeats)
MAX_LOOPS = 20

# Output
OUT_DIR = "data"
SAVE_PARQUET = True
SAVE_CSV = True


# ----------------------------
# HELPERS
# ----------------------------
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def months_ago(dt: datetime, months: int) -> datetime:
    # Simple "months back" approximation: 30 days per month.
    # For trading data, this is usually fine. If you want exact month arithmetic,
    # use python-dateutil.relativedelta.
    return dt - timedelta(days=30 * months)


def init_tv() -> TvDatafeed:
    """
    If you want to use credentials, set environment variables:
      TV_USERNAME, TV_PASSWORD

    Or hardcode them below (not recommended).
    """
    username = os.getenv("TV_USERNAME")
    password = os.getenv("TV_PASSWORD")

    if username and password:
        return TvDatafeed(username=username, password=password)
    else:
        # anonymous login sometimes works but can be rate-limited / restricted
        return TvDatafeed()


def fetch_chunk(
    tv: TvDatafeed,
    symbol: str,
    exchange: str,
    interval: Interval,
    n_bars: int,
    retries: int = 3,
    sleep_s: float = 2.0,
) -> pd.DataFrame:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            df = tv.get_hist(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                n_bars=n_bars,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return df.copy()
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(sleep_s * attempt)
            else:
                raise RuntimeError(f"Failed to fetch chunk after {retries} attempts: {e}") from e
    raise RuntimeError(f"Unexpected fetch failure: {last_err}")


def backfill_last_months_5m(
    tv: TvDatafeed,
    symbol: str,
    exchange: str,
    months_back: int,
    interval: Interval,
    chunk_bars: int,
    max_loops: int,
) -> pd.DataFrame:
    """
    Backfills by repeatedly requesting the most recent `chunk_bars` candles,
    then moving the 'end' backwards using TradingView's `get_hist(..., n_bars=...)`.

    tvDatafeed doesn't expose an official "end time" parameter consistently across versions,
    so the most reliable approach is:
      - fetch recent chunk
      - keep only data older than the earliest already collected
      - repeat until we hit cutoff

    This works because each call returns the "latest n_bars" at the moment of the call.
    We emulate paging by filtering progressively older timestamps.
    """
    # Use UTC-aware timestamps consistently
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30 * months_back)

    all_chunks: list[pd.DataFrame] = []
    oldest_seen = None

    for i in range(max_loops):
        df = fetch_chunk(tv, symbol, exchange, interval, n_bars=chunk_bars)

        if df.empty:
            break

        # Force index to timezone-aware UTC
        df = df.sort_index()
        df.index = pd.to_datetime(df.index, utc=True)

        # If we've already collected some data, only keep rows strictly older than what we have
        if oldest_seen is not None:
            df = df[df.index < oldest_seen]

        if df.empty:
            break

        oldest_seen = df.index.min()
        all_chunks.append(df)

        # ✅ Now this comparison works (both tz-aware)
        if oldest_seen <= cutoff:
            break

        time.sleep(1.0)

    if not all_chunks:
        return pd.DataFrame()

    combined = pd.concat(all_chunks, axis=0)
    combined = combined[~combined.index.duplicated(keep="first")]
    combined = combined.sort_index()

    # Trim exactly to cutoff
    combined = combined[combined.index >= cutoff]

    combined.columns = [c.lower().strip() for c in combined.columns]
    return combined


# ----------------------------
# MAIN
# ----------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    tv = init_tv()
    df = backfill_last_months_5m(
        tv=tv,
        symbol=SYMBOL,
        exchange=EXCHANGE,
        months_back=MONTHS_BACK,
        interval=INTERVAL,
        chunk_bars=CHUNK_BARS,
        max_loops=MAX_LOOPS,
    )

    if df.empty:
        print("No data returned. Possible causes:")
        print("- Wrong exchange/symbol mapping (try EXCHANGE='NYSEARCA' or 'AMEX')")
        print("- Rate limiting / login required")
        print("- tvDatafeed version mismatch")
        return

    # Add a symbol column for convenience
    if "symbol" not in df.columns:
        df.insert(0, "symbol", SYMBOL)


    # Save
    stamp = utc_now().strftime("%Y%m%d_%H%M%S")
    base = f"{SYMBOL}_5m_last_{MONTHS_BACK}mo_{stamp}"

    if SAVE_PARQUET:
        out_parquet = os.path.join(OUT_DIR, base + ".parquet")
        df.to_parquet(out_parquet, index=True)
        print(f"Saved parquet: {out_parquet}")

    if SAVE_CSV:
        out_csv = os.path.join(OUT_DIR, base + ".csv")
        df.to_csv(out_csv, index=True)
        print(f"Saved csv: {out_csv}")

    print(df.head(3))
    print(df.tail(3))
    print(f"Rows: {len(df):,} | From: {df.index.min()} | To: {df.index.max()}")


if __name__ == "__main__":
    main()
