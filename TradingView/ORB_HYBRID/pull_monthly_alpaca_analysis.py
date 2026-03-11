from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

DEFAULT_SYMBOLS_FILE = Path(__file__).resolve().parent / "universes" / "focus_symbols_v1.txt"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "alpaca_monthly_cache.sqlite"
DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "reports" / "monthly_ema20_cross_summary.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pull Alpaca 1Month bars into separate DB and analyze EMA20 monthly close crosses."
    )
    p.add_argument("--symbols", default="")
    p.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS_FILE))
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--report-csv", default=str(DEFAULT_REPORT_PATH))
    p.add_argument("--alpaca-key", default="")
    p.add_argument("--alpaca-secret", default="")
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex")
    p.add_argument("--alpaca-base-url", default="https://data.alpaca.markets")
    p.add_argument("--start", default="2000-01-01T00:00:00Z")
    p.add_argument("--end", default="")
    p.add_argument("--adjustment", choices=["raw", "split", "dividend", "all"], default="split")
    p.add_argument("--timeout-sec", type=int, default=40)
    return p.parse_args()


def _load_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols.strip():
        vals = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return sorted(dict.fromkeys(vals))

    p = Path(args.symbols_file)
    if not p.exists():
        raise FileNotFoundError(f"Symbols file not found: {p}")
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        x = line.strip()
        if not x or x.startswith("#"):
            continue
        sym = x.split(":")[0].strip().upper()
        if sym:
            out.append(sym)
    return sorted(dict.fromkeys(out))


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_bars (
            source_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY (source_key, symbol, timeframe, ts_utc)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_monthly_bars_lookup
        ON monthly_bars (source_key, symbol, timeframe, ts_utc)
        """
    )
    conn.commit()


def _fetch_symbol_monthly(
    *,
    symbol: str,
    key: str,
    secret: str,
    feed: str,
    base_url: str,
    start: str,
    end: str,
    adjustment: str,
    timeout_sec: int,
) -> pd.DataFrame:
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    url = base_url.rstrip("/") + "/v2/stocks/bars"
    page_token = ""
    rows: list[dict] = []

    while True:
        params = {
            "symbols": symbol,
            "timeframe": "1Month",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": adjustment,
            "feed": feed,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=timeout_sec)
        if resp.status_code != 200:
            raise RuntimeError(f"{symbol}: Alpaca API error {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        bars = payload.get("bars", {}).get(symbol, []) or []
        rows.extend(bars)
        page_token = payload.get("next_page_token") or ""
        if not page_token:
            break

    if not rows:
        return pd.DataFrame(columns=["symbol", "ts_utc", "open", "high", "low", "close", "volume"])

    d = pd.DataFrame(rows)
    required = {"t", "o", "h", "l", "c", "v"}
    miss = required - set(d.columns)
    if miss:
        raise RuntimeError(f"{symbol}: missing columns from Alpaca bars: {sorted(miss)}")

    out = pd.DataFrame(
        {
            "symbol": symbol,
            "ts_utc": pd.to_datetime(d["t"], utc=True).astype(str),
            "open": d["o"].astype(float),
            "high": d["h"].astype(float),
            "low": d["l"].astype(float),
            "close": d["c"].astype(float),
            "volume": d["v"].astype(float),
        }
    )
    out = out.sort_values("ts_utc").drop_duplicates(subset=["ts_utc"]).reset_index(drop=True)
    return out


def _store_monthly(
    conn: sqlite3.Connection,
    bars: pd.DataFrame,
    source_key: str,
) -> int:
    if bars.empty:
        return 0
    fetched_at_utc = datetime.now(timezone.utc).isoformat()
    payload = [
        (
            source_key,
            str(r.symbol),
            "1Month",
            str(r.ts_utc),
            float(r.open),
            float(r.high),
            float(r.low),
            float(r.close),
            float(r.volume),
            fetched_at_utc,
        )
        for r in bars.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO monthly_bars
        (source_key, symbol, timeframe, ts_utc, open, high, low, close, volume, fetched_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    conn.commit()
    return len(payload)


def _analyze_monthly_crosses(conn: sqlite3.Connection, source_key: str, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    q_marks = ",".join(["?"] * len(symbols))
    q = f"""
        SELECT symbol, ts_utc, close
        FROM monthly_bars
        WHERE source_key = ? AND timeframe = '1Month' AND symbol IN ({q_marks})
        ORDER BY symbol, ts_utc
    """
    data = pd.read_sql_query(q, conn, params=[source_key] + symbols)
    if data.empty:
        return pd.DataFrame()

    data["ts_utc"] = pd.to_datetime(data["ts_utc"], utc=True, errors="coerce")
    data = data.dropna(subset=["ts_utc"]).copy()

    rows: list[dict] = []
    for sym, part in data.groupby("symbol"):
        z = part.sort_values("ts_utc").reset_index(drop=True).copy()
        if len(z) < 25:
            continue
        z["ema20"] = z["close"].ewm(span=20, adjust=False).mean()
        z["prev_close"] = z["close"].shift(1)
        z["prev_ema20"] = z["ema20"].shift(1)
        z["idx"] = range(len(z))
        z = z[z["idx"] >= 20].copy()
        z["cross_up"] = (z["prev_close"] <= z["prev_ema20"]) & (z["close"] > z["ema20"])
        z["cross_down"] = (z["prev_close"] >= z["prev_ema20"]) & (z["close"] < z["ema20"])
        z["ret_fwd_1m"] = z["close"].shift(-1) / z["close"] - 1.0
        z["ret_fwd_3m"] = z["close"].shift(-3) / z["close"] - 1.0
        ev = z[z["cross_up"] | z["cross_down"]].copy()
        if ev.empty:
            continue
        for r in ev.itertuples(index=False):
            direction = "up" if bool(r.cross_up) else "down"
            for h, col in [(1, "ret_fwd_1m"), (3, "ret_fwd_3m")]:
                val = getattr(r, col)
                if pd.isna(val):
                    continue
                hit = (val > 0.0) if direction == "up" else (val < 0.0)
                rows.append(
                    {
                        "symbol": sym,
                        "direction": direction,
                        "horizon_m": int(h),
                        "fwd_ret": float(val),
                        "hit": int(hit),
                    }
                )
    events = pd.DataFrame(rows)
    if events.empty:
        return pd.DataFrame()

    summary = (
        events.groupby(["direction", "horizon_m"], as_index=False)
        .agg(
            events=("hit", "size"),
            hit_rate=("hit", "mean"),
            avg_fwd_ret=("fwd_ret", "mean"),
            median_fwd_ret=("fwd_ret", "median"),
        )
        .sort_values(["direction", "horizon_m"])
        .reset_index(drop=True)
    )
    summary["hit_rate_pct"] = 100.0 * summary["hit_rate"]
    summary["avg_fwd_ret_pct"] = 100.0 * summary["avg_fwd_ret"]
    summary["median_fwd_ret_pct"] = 100.0 * summary["median_fwd_ret"]
    return summary


def main() -> None:
    args = _parse_args()
    symbols = _load_symbols(args)
    if not symbols:
        raise RuntimeError("No symbols provided.")

    key = (args.alpaca_key or os.getenv("APCA_API_KEY_ID", "")).strip()
    secret = (args.alpaca_secret or os.getenv("APCA_API_SECRET_KEY", "")).strip()
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing. Set APCA_API_KEY_ID/APCA_API_SECRET_KEY or pass flags.")

    end = args.end.strip() or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Month"

    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    _ensure_schema(conn)

    print("=== Pull Monthly Bars ===")
    print(f"symbols={len(symbols)} source_key={source_key}")
    print(f"start={args.start} end={end}")
    print(f"db={db_path}")

    total_rows = 0
    for sym in symbols:
        bars = _fetch_symbol_monthly(
            symbol=sym,
            key=key,
            secret=secret,
            feed=args.alpaca_feed,
            base_url=args.alpaca_base_url,
            start=args.start,
            end=end,
            adjustment=args.adjustment,
            timeout_sec=args.timeout_sec,
        )
        n = _store_monthly(conn, bars, source_key=source_key)
        total_rows += n
        if bars.empty:
            print(f"{sym}: 0 rows")
        else:
            print(f"{sym}: {n} rows | {bars['ts_utc'].min()} -> {bars['ts_utc'].max()}")

    print(f"total_rows_upserted={total_rows}")

    summary = _analyze_monthly_crosses(conn, source_key=source_key, symbols=symbols)
    conn.close()

    print("\n=== EMA20 Monthly Cross Analysis ===")
    if summary.empty:
        print("No events available after warm-up.")
        return

    show_cols = [
        "direction",
        "horizon_m",
        "events",
        "hit_rate_pct",
        "avg_fwd_ret_pct",
        "median_fwd_ret_pct",
    ]
    print(summary[show_cols].to_string(index=False))

    report_path = Path(args.report_csv)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(report_path, index=False)
    print(f"\nSaved summary CSV: {report_path}")


if __name__ == "__main__":
    main()
