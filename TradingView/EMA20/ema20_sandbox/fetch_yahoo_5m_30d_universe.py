# ============================================================
# Module S2: Fetch Yahoo 5-minute bars (last 30 days) for a universe
#
# Inputs:
#   data/symbols.csv
#
# Outputs:
#   data/raw/<SYMBOL>_30d_5m_yahoo.csv
#
# Notes:
# - Yahoo intraday limits can be strict; 30d/5m usually works.
# - We add strong sanity checks:
#   - non-empty
#   - timestamps not 1970
#   - monotonic increasing
#   - expected columns
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Dict, Any

import pandas as pd

from symbols_loader import load_enabled_symbols


@dataclass(frozen=True)
class YahooFetchConfig:
    period: str = "30d"
    interval: str = "5m"
    timezone: str = "America/New_York"
    out_dir: str = "data/raw"
    min_rows_ok: int = 500  # rough sanity threshold for 30d of 5m bars


def fetch_yahoo_intraday(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """
    Uses yfinance to fetch intraday bars.
    """
    try:
        import yfinance as yf
    except ImportError as e:
        raise RuntimeError("Missing dependency: yfinance. Install with: pip install yfinance") from e

    tk = yf.Ticker(symbol)
    df = tk.history(period=period, interval=interval, auto_adjust=False, actions=False)

    # yfinance returns index as tz-aware or naive depending on version/settings
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.reset_index(inplace=True)

    # normalize timestamp col name
    if "Datetime" in df.columns:
        df.rename(columns={"Datetime": "timestamp"}, inplace=True)
    elif "Date" in df.columns:
        df.rename(columns={"Date": "timestamp"}, inplace=True)
    else:
        # fallback: assume first column is timestamp
        df.rename(columns={df.columns[0]: "timestamp"}, inplace=True)

    return df


def normalize_columns(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    """
    Standardize to: timestamp, open, high, low, close, volume
    Convert timestamp to tz-aware in America/New_York.
    """
    if df.empty:
        return df

    col_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    for src, dst in col_map.items():
        if src in df.columns and dst not in df.columns:
            df.rename(columns={src: dst}, inplace=True)

    needed = ["timestamp", "open", "high", "low", "close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"Missing expected columns after normalize: {missing}. Columns: {list(df.columns)}")

    # parse timestamp
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=False)

    # If naive -> assume UTC then convert. If tz-aware -> convert.
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC").dt.tz_convert(tz)
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(tz)

    # drop invalid timestamps
    df = df[df["timestamp"].notna()].copy()

    # keep only required cols
    keep = ["timestamp", "open", "high", "low", "close"]
    if "volume" in df.columns:
        keep.append("volume")
    df = df[keep].copy()

    # sort by time
    df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def sanity_check(symbol: str, df: pd.DataFrame, cfg: YahooFetchConfig) -> Dict[str, Any]:
    """
    Returns status and reasons if failed.
    """
    info = {"symbol": symbol, "ok": True, "rows": int(len(df)), "reason": ""}

    if df.empty:
        info["ok"] = False
        info["reason"] = "EMPTY"
        return info

    # 1970 or absurd old timestamps
    min_ts = df["timestamp"].min()
    if min_ts.year < 2000:
        info["ok"] = False
        info["reason"] = f"BAD_TIMESTAMP(min={min_ts})"
        return info

    # monotonic increasing
    if not df["timestamp"].is_monotonic_increasing:
        info["ok"] = False
        info["reason"] = "TIMESTAMPS_NOT_SORTED"
        return info

    # minimum rows (soft check)
    if len(df) < cfg.min_rows_ok:
        info["ok"] = True
        info["reason"] = f"LOW_ROWS({len(df)})_but_continuing"

    return info


def main():
    cfg = YahooFetchConfig()

    symbols_path = os.path.join(os.getcwd(), "data", "symbols.csv")
    out_dir = os.path.join(os.getcwd(), cfg.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    symbols: List[str] = load_enabled_symbols(symbols_path)
    print("\n[Module S2] Enabled symbols:")
    print(symbols)

    results = []
    for sym in symbols:
        print(f"\nFetching {sym} ({cfg.period}, {cfg.interval})...")
        raw = fetch_yahoo_intraday(sym, cfg.period, cfg.interval)
        df = normalize_columns(raw, cfg.timezone)

        status = sanity_check(sym, df, cfg)
        results.append(status)

        if not status["ok"]:
            print(f"  ❌ FAIL: {status['reason']}")
            continue

        out_path = os.path.join(out_dir, f"{sym}_30d_5m_yahoo.csv")
        df.to_csv(out_path, index=False)
        print(f"  ✅ Saved: {out_path} | rows={len(df):,} | note={status['reason']}")

    # Summary
    summary = pd.DataFrame(results)
    print("\n--- Fetch Summary ---")
    print(summary)

    failed = summary[~summary["ok"]]
    if not failed.empty:
        print("\nSome symbols failed. You can disable them in data/symbols.csv or investigate Yahoo limits.")
    else:
        print("\n✅ Module S2 complete: all enabled symbols fetched successfully.\n")


if __name__ == "__main__":
    main()
