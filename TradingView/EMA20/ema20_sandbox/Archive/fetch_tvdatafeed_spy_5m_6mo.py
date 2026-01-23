"""
Robust SPY 5-minute backfill from TradingView via tvDatafeed.

Key upgrades vs basic scripts:
- Exchange fallback list (AMEX, NYSEARCA, etc.)
- Strong retry/backoff
- Chunk backfill paging (like your working script)
- Writes a "raw" CSV that we then normalize to canonical schema

Requires:
  pip install tvDatafeed pandas
Optional:
  set TV_USERNAME / TV_PASSWORD env vars for reliable access
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pandas as pd
from tvDatafeed import TvDatafeed, Interval


SYMBOL = "SPY"
INTERVAL = Interval.in_5_minute

# Try multiple TV exchange mappings. Your working script uses AMEX first. :contentReference[oaicite:1]{index=1}
EXCHANGE_CANDIDATES = ["AMEX", "NYSEARCA", "ARCA", "NASDAQ", "NYSE"]

MONTHS_BACK = 6
CHUNK_BARS = 5000
MAX_LOOPS = 40

OUT_DIR = "data"
OUT_BASENAME = "SPY_tvdatafeed_5m_raw.csv"


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def init_tv() -> TvDatafeed:
    """
    Uses env vars if present:
      TV_USERNAME, TV_PASSWORD
    """
    username = "vishu723"
    password = "Tradingview123$"
    if username and password:
        print("✅ Using TradingView credentials from env vars.")
        return TvDatafeed(username=username, password=password)

    print("⚠️ Using no-login mode (may be limited / time out). Set TV_USERNAME/TV_PASSWORD if possible.")
    return TvDatafeed()


def fetch_chunk(tv: TvDatafeed, symbol: str, exchange: str, interval: Interval, n_bars: int) -> pd.DataFrame:
    """
    Fetch with retry/backoff.
    """
    last_err = None
    for attempt in range(1, 6):
        try:
            df = tv.get_hist(symbol=symbol, exchange=exchange, interval=interval, n_bars=n_bars)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.sort_index()
            df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
            df = df.dropna(subset=["open", "high", "low", "close"])
            return df
        except Exception as e:
            last_err = e
            sleep_s = 2.0 * attempt
            print(f"  retry {attempt}/5 due to error: {e} | sleeping {sleep_s:.1f}s")
            time.sleep(sleep_s)
    raise RuntimeError(f"Failed to fetch chunk after retries. Last error: {last_err}")


def backfill_months(tv: TvDatafeed, symbol: str, exchange: str) -> pd.DataFrame:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30 * MONTHS_BACK)

    all_chunks: list[pd.DataFrame] = []
    oldest_seen: pd.Timestamp | None = None

    for i in range(MAX_LOOPS):
        df = fetch_chunk(tv, symbol, exchange, INTERVAL, CHUNK_BARS)
        if df.empty:
            break

        if oldest_seen is not None:
            df = df[df.index < oldest_seen]
        if df.empty:
            break

        oldest_seen = df.index.min()
        all_chunks.append(df)

        print(f"  loop {i+1:02d}: got {len(df):,} | oldest_seen={oldest_seen} | newest={df.index.max()}")

        if oldest_seen <= cutoff:
            break

        time.sleep(1.0)

    if not all_chunks:
        return pd.DataFrame()

    combined = pd.concat(all_chunks, axis=0)
    combined = combined[~combined.index.duplicated(keep="first")].sort_index()
    combined = combined[combined.index >= cutoff]
    combined.columns = [c.lower().strip() for c in combined.columns]

    # Add metadata columns only if missing
    if "symbol" not in combined.columns:
        combined.insert(0, "symbol", symbol)
    else:
        combined["symbol"] = combined["symbol"].fillna(symbol)

    if "exchange_used" not in combined.columns:
        combined.insert(1, "exchange_used", exchange)
    else:
        combined["exchange_used"] = combined["exchange_used"].fillna(exchange)

    if "source" not in combined.columns:
        combined.insert(2, "source", "tvdatafeed")
    else:
        combined["source"] = combined["source"].fillna("tvdatafeed")

    return combined


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    tv = init_tv()

    best_df = pd.DataFrame()
    best_exchange = None

    print("\n[TVDatafeed Backfill] Trying exchanges:", EXCHANGE_CANDIDATES)

    for ex in EXCHANGE_CANDIDATES:
        try:
            print(f"\n--- Attempt exchange={ex} ---")
            df = backfill_months(tv, SYMBOL, ex)
            if df.empty:
                print("  -> no data")
                continue

            print(f"  -> SUCCESS: {len(df):,} rows | {df.index.min()} -> {df.index.max()}")
            best_df = df
            best_exchange = ex
            break
        except Exception as e:
            print(f"  -> FAILED for exchange={ex}: {e}")

    if best_df.empty:
        raise RuntimeError(
            "tvDatafeed returned no data for all exchange candidates.\n"
            "Most common fixes:\n"
            "1) Provide TV_USERNAME/TV_PASSWORD env vars (no-login often fails)\n"
            "2) Try again later (rate-limits / transient timeouts)\n"
            "3) Ensure tvDatafeed package version is compatible"
        )

    out_path = os.path.join(OUT_DIR, OUT_BASENAME.replace(".csv", f"_{utc_now_str()}.csv"))
    best_df.to_csv(out_path, index=True)
    print(f"\n✅ Saved raw tvDatafeed bars: {out_path}")
    print(f"Exchange used: {best_exchange}")
    print(best_df.head(3))
    print(best_df.tail(3))
    print(f"Rows: {len(best_df):,}")

if __name__ == "__main__":
    main()
