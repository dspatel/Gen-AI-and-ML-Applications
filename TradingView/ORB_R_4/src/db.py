from __future__ import annotations

import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple, Dict, Any

@dataclass(frozen=True)
class CandleRow:
    symbol: str
    interval: str
    open_ts_utc: str
    close_ts_utc: str
    open_ts_cst: str
    close_ts_cst: str
    cst_date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]
    source: str
    ingested_at_cst: str

def connect(db_path: str) -> sqlite3.Connection:
    # Ensure DB directory exists and resolve relative paths against the project root.
    p = Path(db_path)
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parent.parent
        p = (project_root / p).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_ts_utc TEXT NOT NULL,
            close_ts_utc TEXT NOT NULL,
            open_ts_cst TEXT NOT NULL,
            close_ts_cst TEXT NOT NULL,
            cst_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            ingested_at_cst TEXT NOT NULL,
            PRIMARY KEY (symbol, interval, open_ts_utc)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_candles_symbol_interval_date
        ON candles(symbol, interval, cst_date);
        """
    )
    conn.commit()

def upsert_candles(conn: sqlite3.Connection, rows: Iterable[CandleRow]) -> Tuple[int, int]:
    inserted = 0
    skipped = 0

    sql = """
    INSERT OR IGNORE INTO candles (
        symbol, interval,
        open_ts_utc, close_ts_utc,
        open_ts_cst, close_ts_cst,
        cst_date,
        open, high, low, close, volume,
        source, ingested_at_cst
    )
    VALUES (
        :symbol, :interval,
        :open_ts_utc, :close_ts_utc,
        :open_ts_cst, :close_ts_cst,
        :cst_date,
        :open, :high, :low, :close, :volume,
        :source, :ingested_at_cst
    );
    """

    with conn:
        for r in rows:
            d: Dict[str, Any] = {
                "symbol": r.symbol,
                "interval": r.interval,
                "open_ts_utc": r.open_ts_utc,
                "close_ts_utc": r.close_ts_utc,
                "open_ts_cst": r.open_ts_cst,
                "close_ts_cst": r.close_ts_cst,
                "cst_date": r.cst_date,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
                "source": r.source,
                "ingested_at_cst": r.ingested_at_cst,
            }
            cur = conn.execute(sql, d)
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

    return inserted, skipped
