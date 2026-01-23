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


def ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    """Ensure columns exist on table. columns: {name: sql_type}"""
    cur = conn.execute(f"PRAGMA table_info({table});")
    existing = {r[1] for r in cur.fetchall()}
    for name, sql_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type};")
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

            window_high_7 REAL,
            window_low_7  REAL,

            window_high_21 REAL,
            window_low_21  REAL,

            armed INTEGER NOT NULL DEFAULT 1,

            last_alert_date TEXT,
            last_alert_signal TEXT,

            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # Migrate older DBs safely
    ensure_columns(conn, 'symbol_state', {
        'window_high_7':'REAL',
        'window_low_7':'REAL',
        'window_high_21':'REAL',
        'window_low_21':'REAL',
    })

    conn.commit()


def init_alerts_log(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            event_date TEXT NOT NULL,   -- YYYY-MM-DD
            event_time TEXT,            -- HH:MM:SS (local)
            signal TEXT NOT NULL,       -- LONG/SHORT
            source TEXT NOT NULL,       -- LIVE/EOD/BACKTEST
            cross_date TEXT,            -- anchor used
            cross_direction TEXT,
            close REAL,
            ema20 REAL,
            ema20_h REAL,
            ema20_l REAL,
            window_high_7 REAL,
            window_low_7 REAL,
            window_high_21 REAL,
            window_low_21 REAL,
            break_pct_7 REAL,
            break_pct_21 REAL,
            ema_dist REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(symbol, event_date, signal, cross_date)
        );
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_alerts_log_event_date
        ON alerts_log(event_date);
    """)
    conn.commit()

def insert_alert_log(conn: sqlite3.Connection, alert: Dict[str, Any], source: str) -> bool:
    """Insert alert into ledger. Returns True if inserted, False if duplicate."""
    try:
        conn.execute("""
            INSERT OR IGNORE INTO alerts_log(
                symbol, event_date, event_time, signal, source,
                cross_date, cross_direction,
                close, ema20, ema20_h, ema20_l,
                window_high_7, window_low_7, window_high_21, window_low_21,
                break_pct_7, break_pct_21, ema_dist
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """, (
            alert.get('Symbol'),
            alert.get('EventDate'),
            alert.get('EventTime'),
            alert.get('Signal'),
            source,
            alert.get('LatestCrossDate'),
            alert.get('LatestCrossDirection'),
            alert.get('TodayClose'),
            alert.get('EMA20'),
            alert.get('EMA20_H'),
            alert.get('EMA20_L'),
            alert.get('WindowHigh_7D_preCross'),
            alert.get('WindowLow_7D_preCross'),
            alert.get('WindowHigh_21D_preCross'),
            alert.get('WindowLow_21D_preCross'),
            alert.get('BreakPctOfRange_7D'),
            alert.get('BreakPctOfRange_21D'),
            alert.get('EmaDistance'),
        ))
        conn.commit()
        # Determine if inserted
        cur = conn.execute("SELECT changes();")
        return cur.fetchone()[0] == 1
    except Exception:
        conn.rollback()
        raise

def read_alerts_for_date(conn: sqlite3.Connection, event_date: str) -> pd.DataFrame:
    df = pd.read_sql("""
        SELECT
          symbol as Symbol,
          event_date as EventDate,
          event_time as EventTime,
          signal as Signal,
          source as Source,
          cross_date as LatestCrossDate,
          cross_direction as LatestCrossDirection,
          close as TodayClose,
          ema20 as EMA20,
          ema20_h as EMA20_H,
          ema20_l as EMA20_L,
          window_high_7 as WindowHigh_7D_preCross,
          window_low_7 as WindowLow_7D_preCross,
          window_high_21 as WindowHigh_21D_preCross,
          window_low_21 as WindowLow_21D_preCross,
          break_pct_7 as BreakPctOfRange_7D,
          break_pct_21 as BreakPctOfRange_21D,
          ema_dist as EmaDistance,
          created_at as LoggedAt
        FROM alerts_log
        WHERE event_date = ?
        ORDER BY created_at ASC;
    """, conn, params=(event_date,))
    return df

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
            window_high_7, window_low_7,
            window_high_21, window_low_21,
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
        "window_high_7": row[5],
        "window_low_7": row[6],
        "window_high_21": row[7],
        "window_low_21": row[8],
        "armed": int(row[9]) if row[9] is not None else 1,
        "last_alert_date": row[10],
        "last_alert_signal": row[11],
    }

def upsert_symbol_state(
    conn: sqlite3.Connection,
    symbol: str,
    last_cross_date: str,
    last_cross_direction: str,
    window_high: float,
    window_low: float,
    armed: int,
    window_high_7: Optional[float] = None,
    window_low_7: Optional[float] = None,
    window_high_21: Optional[float] = None,
    window_low_21: Optional[float] = None,
    last_alert_date: Optional[str] = None,
    last_alert_signal: Optional[str] = None,
) -> None:
    """
    Upsert full state. window_high/window_low kept for backward compatibility.
    window_high_7/window_low_7 and window_high_21/window_low_21 are the preferred frozen windows.
    Provide last_alert_* only when updating after an alert.
    """
    conn.execute("""
        INSERT INTO symbol_state (
            symbol, last_cross_date, last_cross_direction,
            window_high, window_low,
            window_high_7, window_low_7,
            window_high_21, window_low_21,
            armed,
            last_alert_date, last_alert_signal
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            last_cross_date=excluded.last_cross_date,
            last_cross_direction=excluded.last_cross_direction,
            window_high=excluded.window_high,
            window_low=excluded.window_low,
            window_high_7=COALESCE(excluded.window_high_7, symbol_state.window_high_7),
            window_low_7=COALESCE(excluded.window_low_7, symbol_state.window_low_7),
            window_high_21=COALESCE(excluded.window_high_21, symbol_state.window_high_21),
            window_low_21=COALESCE(excluded.window_low_21, symbol_state.window_low_21),
            armed=excluded.armed,
            last_alert_date=COALESCE(excluded.last_alert_date, symbol_state.last_alert_date),
            last_alert_signal=COALESCE(excluded.last_alert_signal, symbol_state.last_alert_signal),
            updated_at=datetime('now');
    """, (
        symbol, last_cross_date, last_cross_direction,
        float(window_high), float(window_low),
        None if window_high_7 is None else float(window_high_7),
        None if window_low_7 is None else float(window_low_7),
        None if window_high_21 is None else float(window_high_21),
        None if window_low_21 is None else float(window_low_21),
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
