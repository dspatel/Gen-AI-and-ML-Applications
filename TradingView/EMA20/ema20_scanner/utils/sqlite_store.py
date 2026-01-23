import os
import sqlite3
import pandas as pd
from typing import Optional, Dict, Any

def connect_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection, wal_mode: bool = True) -> None:
    if wal_mode:
        conn.execute("PRAGMA journal_mode = WAL;")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol   TEXT NOT NULL,
            date     TEXT NOT NULL,     -- YYYY-MM-DD

            open     REAL NOT NULL,
            high     REAL NOT NULL,
            low      REAL NOT NULL,
            close    REAL NOT NULL,
            volume   REAL NOT NULL,

            ema20    REAL,
            ema20_h  REAL,
            ema20_l  REAL,

            PRIMARY KEY (symbol, date)
        );
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date
        ON daily_bars(symbol, date);
    """)

    conn.commit()

def init_state_tables(conn: sqlite3.Connection) -> None:
    """
    Stores crossover cycle anchor, frozen window, and arming state.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS symbol_state (
            symbol TEXT PRIMARY KEY,

            last_cross_date TEXT,
            last_cross_direction TEXT,

            window_high REAL,
            window_low  REAL,

            armed INTEGER NOT NULL DEFAULT 1,

            last_alert_date TEXT,
            last_alert_signal TEXT,

            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

def read_daily_bars(conn: sqlite3.Connection, symbol: str, limit_rows: int) -> pd.DataFrame:
    q = """
      SELECT
        date,
        open,
        high,
        low,
        close,
        volume,
        ema20,
        ema20_h,
        ema20_l
      FROM daily_bars
      WHERE symbol = ?
      ORDER BY date DESC
      LIMIT ?;
    """
    df = pd.read_sql_query(q, conn, params=(symbol, limit_rows))
    if df.empty:
        return df

    df = df.rename(columns={
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "ema20": "EMA20",
        "ema20_h": "EMA20_H",
        "ema20_l": "EMA20_L",
    })
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)

def get_symbol_state(conn: sqlite3.Connection, symbol: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT
            symbol, last_cross_date, last_cross_direction,
            window_high, window_low,
            armed,
            last_alert_date, last_alert_signal
        FROM symbol_state
        WHERE symbol = ?;
    """, (symbol,)).fetchone()

    if not row:
        return None

    return {
        "symbol": row[0],
        "last_cross_date": row[1],
        "last_cross_direction": row[2],
        "window_high": row[3],
        "window_low": row[4],
        "armed": int(row[5]),
        "last_alert_date": row[6],
        "last_alert_signal": row[7],
    }

def upsert_symbol_state(
    conn: sqlite3.Connection,
    symbol: str,
    last_cross_date: str,
    last_cross_direction: str,
    window_high: float,
    window_low: float,
    armed: int,
    last_alert_date: Optional[str] = None,
    last_alert_signal: Optional[str] = None,
) -> None:
    """
    Upsert full state. Provide last_alert_* only when updating after an alert.
    """
    conn.execute("""
        INSERT INTO symbol_state (
            symbol, last_cross_date, last_cross_direction,
            window_high, window_low,
            armed,
            last_alert_date, last_alert_signal
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            last_cross_date=excluded.last_cross_date,
            last_cross_direction=excluded.last_cross_direction,
            window_high=excluded.window_high,
            window_low=excluded.window_low,
            armed=excluded.armed,
            last_alert_date=COALESCE(excluded.last_alert_date, symbol_state.last_alert_date),
            last_alert_signal=COALESCE(excluded.last_alert_signal, symbol_state.last_alert_signal),
            updated_at=datetime('now');
    """, (
        symbol, last_cross_date, last_cross_direction,
        float(window_high), float(window_low),
        int(armed),
        last_alert_date, last_alert_signal
    ))
    conn.commit()

def set_armed(conn: sqlite3.Connection, symbol: str, armed: int) -> None:
    conn.execute("""
        UPDATE symbol_state
        SET armed=?, updated_at=datetime('now')
        WHERE symbol=?;
    """, (int(armed), symbol))
    conn.commit()

def set_alert_info(conn: sqlite3.Connection, symbol: str, alert_date: str, alert_signal: str) -> None:
    conn.execute("""
        UPDATE symbol_state
        SET last_alert_date=?, last_alert_signal=?, updated_at=datetime('now')
        WHERE symbol=?;
    """, (alert_date, alert_signal, symbol))
    conn.commit()
