from __future__ import annotations

import sqlite3
from pathlib import Path


def _resolve_source_db_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / p).resolve()


def copy_15m_candles_from_r6(
    conn: sqlite3.Connection,
    source_db_path: str,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, int | str]:
    src_path = _resolve_source_db_path(source_db_path)
    if not src_path.exists():
        raise FileNotFoundError(f"R6 source DB not found: {src_path}")

    src = sqlite3.connect(str(src_path))
    copied = 0
    source_rows = 0
    try:
        for sym in symbols:
            df = _read_symbol_range(src, sym, start_date, end_date)
            if df.empty:
                continue
            source_rows += int(len(df))
            rows = [
                (
                    str(r["symbol"]).upper(),
                    "15m",
                    str(r["open_ts_utc"]),
                    str(r["close_ts_utc"]),
                    str(r["open_ts_cst"]),
                    str(r["close_ts_cst"]),
                    str(r["cst_date"]),
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    (None if r["volume"] is None else float(r["volume"])),
                    "r6_cache_copy",
                    str(r["ingested_at_cst"]),
                )
                for _, r in df.iterrows()
            ]
            conn.executemany(
                """
                INSERT OR REPLACE INTO candles(
                    symbol, interval, open_ts_utc, close_ts_utc,
                    open_ts_cst, close_ts_cst, cst_date,
                    open, high, low, close, volume, source, ingested_at_cst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            copied += int(len(rows))
        conn.commit()
    finally:
        src.close()

    return {
        "source_db": str(src_path),
        "symbols": len(symbols),
        "source_rows": source_rows,
        "copied_rows": copied,
    }


def _read_symbol_range(src: sqlite3.Connection, symbol: str, start_date: str, end_date: str):
    import pandas as pd

    query = """
    SELECT
      symbol,
      open_ts_utc,
      close_ts_utc,
      open_ts_cst,
      close_ts_cst,
      cst_date,
      open, high, low, close, volume,
      ingested_at_cst
    FROM candles
    WHERE symbol = ?
      AND interval = '15m'
      AND cst_date >= ?
      AND cst_date <= ?
    ORDER BY open_ts_utc
    """
    return pd.read_sql_query(query, src, params=(symbol, start_date, end_date))

