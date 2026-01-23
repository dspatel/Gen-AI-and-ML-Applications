# ============================================================
# Module S3: Canonicalize raw Yahoo intraday data into a standard schema
#
# Inputs:
#   data/symbols.csv
#   data/raw/<SYMBOL>_30d_5m_yahoo.csv
#
# Outputs:
#   data/canonical/<SYMBOL>_30d_5m_canonical.csv
#   data/canonical/canonicalization_report.csv
#
# Canonical schema:
#   timestamp (tz-aware, America/New_York)
#   symbol
#   open, high, low, close (float)
#   volume (float/int; default 0)
#   session_date (YYYY-MM-DD in NY timezone)
#   source (e.g., 'yahoo')
#   bar_interval (e.g., '5m')
#
# Run:
#   python canonicalize_universe.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pandas as pd

from symbols_loader import load_symbols_csv


@dataclass(frozen=True)
class CanonicalConfig:
    timezone: str = "America/New_York"
    raw_dir: str = "data/raw"
    out_dir: str = "data/canonical"
    source: str = "yahoo"
    bar_interval: str = "5m"

    # Validation
    drop_non_rth: bool = False          # for now keep all bars; later can filter 09:30-16:00 ET
    min_year_ok: int = 2000
    require_ohlc: bool = True
    enforce_positive_prices: bool = True


CANON_COLS = [
    "timestamp", "symbol",
    "open", "high", "low", "close",
    "volume",
    "session_date",
    "source", "bar_interval",
]


def _parse_timestamp(ts: pd.Series, tz: str) -> pd.Series:
    t = pd.to_datetime(ts, errors="coerce", utc=False)

    # If naive -> assume UTC then convert. If tz-aware -> convert.
    if getattr(t.dt, "tz", None) is None:
        t = t.dt.tz_localize("UTC").dt.tz_convert(tz)
    else:
        t = t.dt.tz_convert(tz)

    return t


def _compute_session_date(ts: pd.Series) -> pd.Series:
    # session_date as YYYY-MM-DD based on NY time
    return ts.dt.strftime("%Y-%m-%d")


def _filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    # 09:30-16:00 ET
    t = df["timestamp"]
    hhmm = t.dt.strftime("%H:%M")
    return df[(hhmm >= "09:30") & (hhmm <= "16:00")].copy()


def canonicalize_symbol_file(
    symbol: str,
    raw_path: str,
    cfg: CanonicalConfig,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "symbol": symbol,
        "raw_path": raw_path,
        "ok": True,
        "rows_in": 0,
        "rows_out": 0,
        "reason": "",
        "min_ts": None,
        "max_ts": None,
        "has_volume": False,
    }

    if not os.path.exists(raw_path):
        result["ok"] = False
        result["reason"] = "RAW_FILE_NOT_FOUND"
        return result

    df = pd.read_csv(raw_path)
    result["rows_in"] = int(len(df))

    if df.empty:
        result["ok"] = False
        result["reason"] = "EMPTY_RAW"
        return result

    # Required raw cols
    required_raw = {"timestamp", "open", "high", "low", "close"}
    missing = required_raw - set(df.columns)
    if missing and cfg.require_ohlc:
        result["ok"] = False
        result["reason"] = f"MISSING_COLS:{sorted(missing)}"
        return result

    # Timestamp parsing + tz
    df["timestamp"] = _parse_timestamp(df["timestamp"], cfg.timezone)
    df = df[df["timestamp"].notna()].copy()

    if df.empty:
        result["ok"] = False
        result["reason"] = "ALL_BAD_TIMESTAMPS"
        return result

    # Validate timestamp year
    min_ts = df["timestamp"].min()
    if min_ts.year < cfg.min_year_ok:
        result["ok"] = False
        result["reason"] = f"BAD_TIMESTAMP(min={min_ts})"
        return result

    # Numeric coercions
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        result["has_volume"] = True
    else:
        df["volume"] = 0
        result["has_volume"] = False

    # Drop rows with invalid OHLC
    if cfg.require_ohlc:
        df = df.dropna(subset=["open", "high", "low", "close"]).copy()

    if df.empty:
        result["ok"] = False
        result["reason"] = "ALL_BAD_OHLC"
        return result

    # Enforce positive prices
    if cfg.enforce_positive_prices:
        df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].copy()

    if df.empty:
        result["ok"] = False
        result["reason"] = "NONPOSITIVE_PRICES"
        return result

    # Optional RTH filter
    if cfg.drop_non_rth:
        df = _filter_rth(df)

    if df.empty:
        result["ok"] = False
        result["reason"] = "NO_RTH_ROWS"
        return result

    # Add canonical fields
    df["symbol"] = symbol
    df["session_date"] = _compute_session_date(df["timestamp"])
    df["source"] = cfg.source
    df["bar_interval"] = cfg.bar_interval

    # Keep canonical columns only
    df = df[CANON_COLS].copy()

    # Sort and dedupe timestamps
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp", "symbol"], keep="last").reset_index(drop=True)

    result["rows_out"] = int(len(df))
    result["min_ts"] = str(df["timestamp"].min())
    result["max_ts"] = str(df["timestamp"].max())

    # Save
    os.makedirs(os.path.join(os.getcwd(), cfg.out_dir), exist_ok=True)
    out_path = os.path.join(os.getcwd(), cfg.out_dir, f"{symbol}_30d_5m_canonical.csv")
    df.to_csv(out_path, index=False)

    result["out_path"] = out_path
    result["reason"] = "OK"
    return result


def main():
    cfg = CanonicalConfig()

    symbols_csv = os.path.join(os.getcwd(), "data", "symbols.csv")
    raw_dir = os.path.join(os.getcwd(), cfg.raw_dir)
    out_dir = os.path.join(os.getcwd(), cfg.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Load enabled symbols
    df_symbols = load_symbols_csv(symbols_csv, enabled_only=True)
    symbols = df_symbols["symbol"].tolist()

    print("\n[Module S3] Canonicalizing symbols:")
    print(symbols)

    report_rows: List[Dict[str, Any]] = []

    for sym in symbols:
        raw_path = os.path.join(raw_dir, f"{sym}_30d_5m_yahoo.csv")
        print(f"\nCanonicalizing {sym} from {raw_path}")
        r = canonicalize_symbol_file(sym, raw_path, cfg)
        report_rows.append(r)
        if r["ok"]:
            print(f"  ✅ OK rows_in={r['rows_in']:,} rows_out={r['rows_out']:,} [{r['min_ts']} → {r['max_ts']}]")
        else:
            print(f"  ❌ FAIL: {r['reason']}")

    report = pd.DataFrame(report_rows)
    report_path = os.path.join(out_dir, "canonicalization_report.csv")
    report.to_csv(report_path, index=False)

    print("\n--- Canonicalization Report ---")
    print(report[["symbol", "ok", "rows_in", "rows_out", "reason"]])

    failed = report[~report["ok"]]
    if not failed.empty:
        print("\nSome symbols failed canonicalization. You can disable them in data/symbols.csv or inspect raw files.")
    else:
        print("\n✅ Module S3 complete: all enabled symbols canonicalized.\n")

    print(f"Saved report: {report_path}\n")


if __name__ == "__main__":
    main()
