import os
import sqlite3
import pandas as pd
from typing import Optional, Dict, Any

# =============================================================
# SQLite storage layer
#   - daily_bars: cached DAILY candles + EMA metrics
#   - symbol_state: per-symbol anchor (latest cross) + frozen windows + arming state
#   - alerts_log: durable ledger (LIVE + EOD + BACKTEST) with de-duplication
#
# NOTE: This project is designed for a "nuke DB" workflow when schema changes.
# =============================================================

def connect_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection, wal_mode: bool = True) -> None:
    if wal_mode:
        conn.execute("PRAGMA journal_mode = WAL;")

    conn.execute(
        """
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
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date
        ON daily_bars(symbol, date);
        """
    )

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
    """Create symbol_state table (authoritative production schema)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_state (
            symbol TEXT PRIMARY KEY,

            -- Latest EMA20 range-cross info used as the anchor
            last_cross_date TEXT,
            last_cross_direction TEXT,

            -- Frozen windows anchored to last_cross_date (computed from PRE-cross candles)
            window_days_primary INTEGER,
            window_high_primary REAL,
            window_low_primary  REAL,

            window_days_secondary INTEGER,
            window_high_secondary REAL,
            window_low_secondary  REAL,

            -- State machine flags
            armed INTEGER NOT NULL DEFAULT 1,   -- 1=armed, 0=disarmed

            -- Book-keeping
            last_alert_date TEXT,
            last_alert_signal TEXT,

            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    # For safety if someone runs against an older DB file (should be nuked, but still)
    ensure_columns(conn, "symbol_state", {
        "window_days_primary": "INTEGER",
        "window_high_primary": "REAL",
        "window_low_primary": "REAL",
        "window_days_secondary": "INTEGER",
        "window_high_secondary": "REAL",
        "window_low_secondary": "REAL",
        "armed": "INTEGER",
        "last_alert_date": "TEXT",
        "last_alert_signal": "TEXT",
        "updated_at": "TEXT",
    })

    conn.commit()


def init_alerts_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            event_date TEXT NOT NULL,   -- YYYY-MM-DD
            event_time TEXT,            -- HH:MM:SS (local)
            signal TEXT NOT NULL,       -- LONG/SHORT
            source TEXT NOT NULL,       -- LIVE/EOD/BACKTEST

            -- Anchor
            cross_date TEXT,
            cross_direction TEXT,

            -- Market context
            close REAL,
            ema20 REAL,
            ema20_h REAL,
            ema20_l REAL,
            candle_time TEXT,

            -- Frozen windows (explicit days to avoid confusion)
            window_days_primary INTEGER,
            window_high_primary REAL,
            window_low_primary REAL,

            window_days_secondary INTEGER,
            window_high_secondary REAL,
            window_low_secondary REAL,

            -- Metrics
            break_pct_primary REAL,
            break_pct_secondary REAL,
            ema_dist REAL,

            created_at TEXT NOT NULL DEFAULT (datetime('now')),

            -- De-dupe key: one alert per (symbol, day, signal, anchor cross)
            UNIQUE(symbol, event_date, signal, cross_date)
        );
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerts_log_event_date
        ON alerts_log(event_date);
        """
    )

    ensure_columns(conn, "alerts_log", {
        "candle_time": "TEXT",
        "window_days_primary": "INTEGER",
        "window_high_primary": "REAL",
        "window_low_primary": "REAL",
        "window_days_secondary": "INTEGER",
        "window_high_secondary": "REAL",
        "window_low_secondary": "REAL",
        "break_pct_primary": "REAL",
        "break_pct_secondary": "REAL",
        "ema_dist": "REAL",
    })

    conn.commit()


def insert_alert_log(conn: sqlite3.Connection, alert: Dict[str, Any], source: str) -> bool:
    """Insert alert into ledger. Returns True if inserted, False if duplicate."""

    def g(*keys, default=None):
        for k in keys:
            if k in alert and alert.get(k) is not None:
                return alert.get(k)
        return default

    symbol = g("symbol", "Symbol")
    event_date = g("event_date", "EventDate")
    event_time = g("event_time", "EventTime")
    candle_time = g("candle_time", "CandleTime")
    signal = g("signal", "Signal")
    cross_date = g("cross_date", "LatestCrossDate")
    cross_direction = g("cross_direction", "LatestCrossDirection")

    close = g("close", "TodayClose")
    ema20 = g("ema20", "EMA20")
    ema20_h = g("ema20_h", "EMA20_H")
    ema20_l = g("ema20_l", "EMA20_L")

    d1 = g("window_days_primary", "PrimaryWindowDaysUsed", "WindowDays_Primary", "WindowDaysPrimary")
    d2 = g("window_days_secondary", "SecondaryWindowDaysUsed", "WindowDays_Secondary", "WindowDaysSecondary")

    window_high_primary = g("window_high_primary", "WindowHigh_Primary_preCross")
    window_low_primary = g("window_low_primary", "WindowLow_Primary_preCross")
    break_pct_primary = g("break_pct_primary", "BreakPct_Primary")
    try:
        if d1 is not None:
            d1i = int(d1)
            window_high_primary = window_high_primary if window_high_primary is not None else g(f"WindowHigh_{d1i}D_preCross")
            window_low_primary = window_low_primary if window_low_primary is not None else g(f"WindowLow_{d1i}D_preCross")
            break_pct_primary = break_pct_primary if break_pct_primary is not None else g(f"BreakPct_{d1i}D")
    except Exception:
        pass

    window_high_secondary = g("window_high_secondary", "WindowHigh_Secondary_preCross")
    window_low_secondary = g("window_low_secondary", "WindowLow_Secondary_preCross")
    break_pct_secondary = g("break_pct_secondary", "BreakPct_Secondary")
    try:
        if d2 is not None:
            d2i = int(d2)
            window_high_secondary = window_high_secondary if window_high_secondary is not None else g(f"WindowHigh_{d2i}D_preCross")
            window_low_secondary = window_low_secondary if window_low_secondary is not None else g(f"WindowLow_{d2i}D_preCross")
            break_pct_secondary = break_pct_secondary if break_pct_secondary is not None else g(f"BreakPct_{d2i}D")
    except Exception:
        pass

    ema_dist = g("ema_dist", "EmaDist", "EmaDistance")

    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO alerts_log(
                symbol, event_date, event_time, signal, source,
                cross_date, cross_direction,
                close, ema20, ema20_h, ema20_l,
                candle_time,
                window_days_primary, window_high_primary, window_low_primary,
                window_days_secondary, window_high_secondary, window_low_secondary,
                break_pct_primary, break_pct_secondary, ema_dist
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
            """,
            (
                symbol, event_date, event_time, signal, source,
                cross_date, cross_direction,
                close, ema20, ema20_h, ema20_l,
                candle_time,
                d1, window_high_primary, window_low_primary,
                d2, window_high_secondary, window_low_secondary,
                break_pct_primary, break_pct_secondary, ema_dist,
            ),
        )
        conn.commit()
        cur = conn.execute("SELECT changes();")
        return cur.fetchone()[0] == 1
    except Exception:
        conn.rollback()
        raise


def read_alerts_for_date(conn: sqlite3.Connection, event_date: str) -> pd.DataFrame:
    return pd.read_sql(
        """
        SELECT
          symbol as Symbol,
          event_date as EventDate,
          event_time as EventTime,
          candle_time as CandleTime,
          signal as Signal,
          source as Source,
          cross_date as LatestCrossDate,
          cross_direction as LatestCrossDirection,
          close as TodayClose,
          ema20 as EMA20,
          ema20_h as EMA20_H,
          ema20_l as EMA20_L,

          window_days_primary as PrimaryWindowDaysUsed,
          window_high_primary as WindowHigh_Primary_preCross,
          window_low_primary as WindowLow_Primary_preCross,

          window_days_secondary as SecondaryWindowDaysUsed,
          window_high_secondary as WindowHigh_Secondary_preCross,
          window_low_secondary as WindowLow_Secondary_preCross,

          break_pct_primary as BreakPct_Primary,
          break_pct_secondary as BreakPct_Secondary,
          ema_dist as EmaDist
        FROM alerts_log
        WHERE event_date = ?
        ORDER BY created_at ASC;
        """,
        conn,
        params=(event_date,),
    )


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
    row = conn.execute(
        """
        SELECT
            symbol, last_cross_date, last_cross_direction,
            window_days_primary, window_high_primary, window_low_primary,
            window_days_secondary, window_high_secondary, window_low_secondary,
            armed,
            last_alert_date, last_alert_signal
        FROM symbol_state
        WHERE symbol = ?;
        """,
        (symbol,),
    ).fetchone()

    if not row:
        return None

    return {
        "symbol": row[0],
        "last_cross_date": row[1],
        "last_cross_direction": row[2],
        "window_days_primary": row[3],
        "window_high_primary": row[4],
        "window_low_primary": row[5],
        "window_days_secondary": row[6],
        "window_high_secondary": row[7],
        "window_low_secondary": row[8],
        "armed": int(row[9]) if row[9] is not None else 1,
        "last_alert_date": row[10],
        "last_alert_signal": row[11],
    }


def upsert_symbol_state(
    conn: sqlite3.Connection,
    symbol: str,
    last_cross_date: str,
    last_cross_direction: str,
    armed: int,
    window_days_primary: int,
    window_high_primary: float,
    window_low_primary: float,
    window_days_secondary: Optional[int] = None,
    window_high_secondary: Optional[float] = None,
    window_low_secondary: Optional[float] = None,
    last_alert_date: Optional[str] = None,
    last_alert_signal: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO symbol_state (
            symbol, last_cross_date, last_cross_direction,
            window_days_primary, window_high_primary, window_low_primary,
            window_days_secondary, window_high_secondary, window_low_secondary,
            armed,
            last_alert_date, last_alert_signal
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            last_cross_date=excluded.last_cross_date,
            last_cross_direction=excluded.last_cross_direction,
            window_days_primary=excluded.window_days_primary,
            window_high_primary=excluded.window_high_primary,
            window_low_primary=excluded.window_low_primary,
            window_days_secondary=excluded.window_days_secondary,
            window_high_secondary=excluded.window_high_secondary,
            window_low_secondary=excluded.window_low_secondary,
            armed=excluded.armed,
            last_alert_date=COALESCE(excluded.last_alert_date, symbol_state.last_alert_date),
            last_alert_signal=COALESCE(excluded.last_alert_signal, symbol_state.last_alert_signal),
            updated_at=datetime('now');
        """,
        (
            symbol, last_cross_date, last_cross_direction,
            int(window_days_primary), float(window_high_primary), float(window_low_primary),
            None if window_days_secondary is None else int(window_days_secondary),
            None if window_high_secondary is None else float(window_high_secondary),
            None if window_low_secondary is None else float(window_low_secondary),
            int(armed),
            last_alert_date, last_alert_signal,
        ),
    )
    conn.commit()


def set_armed(conn: sqlite3.Connection, symbol: str, armed: int) -> None:
    conn.execute(
        """
        UPDATE symbol_state
        SET armed=?, updated_at=datetime('now')
        WHERE symbol=?;
        """,
        (int(armed), symbol),
    )
    conn.commit()


def set_alert_info(conn: sqlite3.Connection, symbol: str, alert_date: str, alert_signal: str) -> None:
    conn.execute(
        """
        UPDATE symbol_state
        SET last_alert_date=?, last_alert_signal=?, updated_at=datetime('now')
        WHERE symbol=?;
        """,
        (alert_date, alert_signal, symbol),
    )
    conn.commit()


def upsert_daily_bars(conn: sqlite3.Connection, symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return

    # normalize column names
    colmap = {c: c for c in df.columns}
    for a, b in [
        ("Date", "date"), ("Open", "open"), ("High", "high"), ("Low", "low"),
        ("Close", "close"), ("Volume", "volume"),
        ("EMA20", "ema20"), ("EMA20_H", "ema20_h"), ("EMA20_L", "ema20_l"),
    ]:
        if a in df.columns and b not in df.columns:
            colmap[a] = b
    tmp = df.rename(columns=colmap).copy()

    required = ["date", "open", "high", "low", "close", "volume"]
    for c in required:
        if c not in tmp.columns:
            raise ValueError(f"Missing required column for upsert_daily_bars: {c}")

    tmp["date"] = pd.to_datetime(tmp["date"]).dt.strftime("%Y-%m-%d")

    rows = []
    for _, r in tmp.iterrows():
        rows.append((
            symbol,
            r["date"],
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            float(r["volume"]),
            None if pd.isna(r.get("ema20")) else float(r.get("ema20")),
            None if pd.isna(r.get("ema20_h")) else float(r.get("ema20_h")),
            None if pd.isna(r.get("ema20_l")) else float(r.get("ema20_l")),
        ))

    conn.executemany(
        """
        INSERT INTO daily_bars(symbol, date, open, high, low, close, volume, ema20, ema20_h, ema20_l)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            ema20=excluded.ema20,
            ema20_h=excluded.ema20_h,
            ema20_l=excluded.ema20_l;
        """,
        rows,
    )
    conn.commit()


def prune_symbol_to_last_n(conn: sqlite3.Connection, symbol: str, keep_n: int) -> None:
    conn.execute(
        """
        DELETE FROM daily_bars
        WHERE symbol=?
          AND date NOT IN (
              SELECT date FROM daily_bars
              WHERE symbol=?
              ORDER BY date DESC
              LIMIT ?
          );
        """,
        (symbol, symbol, int(keep_n)),
    )
    conn.commit()


if __name__ == "__main__":
    raise SystemExit(
        "Do not run sqlite_store.py directly. Import and call connect_db/init_db/init_state_tables/init_alerts_log."
    )
