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

            -- Trigger candle (intraday) OHLC at alert time
            candle_open REAL,
            candle_high REAL,
            candle_low  REAL,
            candle_close REAL,

            -- Day (RTH session) OHLC as known at alert time
            day_open_at_alert REAL,
            day_high_at_alert REAL,
            day_low_at_alert  REAL,
            day_close_at_alert REAL,

            -- Final daily OHLC after market close (filled during EOD finalization)
            day_open_final REAL,
            day_high_final REAL,
            day_low_final  REAL,
            day_close_final REAL,

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

            -- EMA20 cross stats (feature engineering)
            ema20_cross_lookback_td INTEGER,
            ema20_cross_count_total INTEGER,
            ema20_cross_count_bull INTEGER,
            ema20_cross_count_bear INTEGER,
            ema20_cross_days_since_last INTEGER,
            ema20_cross_density REAL,

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
        "candle_open": "REAL",
        "candle_high": "REAL",
        "candle_low": "REAL",
        "candle_close": "REAL",
        "day_open_at_alert": "REAL",
        "day_high_at_alert": "REAL",
        "day_low_at_alert": "REAL",
        "day_close_at_alert": "REAL",
        "day_open_final": "REAL",
        "day_high_final": "REAL",
        "day_low_final": "REAL",
        "day_close_final": "REAL",
        "window_days_primary": "INTEGER",
        "window_high_primary": "REAL",
        "window_low_primary": "REAL",
        "window_days_secondary": "INTEGER",
        "window_high_secondary": "REAL",
        "window_low_secondary": "REAL",
        "break_pct_primary": "REAL",
        "break_pct_secondary": "REAL",
        "ema_dist": "REAL",
        "ema20_cross_lookback_td": "INTEGER",
        "ema20_cross_count_total": "INTEGER",
        "ema20_cross_count_bull": "INTEGER",
        "ema20_cross_count_bear": "INTEGER",
        "ema20_cross_days_since_last": "INTEGER",
        "ema20_cross_density": "REAL",

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
    candle_open = g("candle_open", "CandleOpen")
    candle_high = g("candle_high", "CandleHigh")
    candle_low = g("candle_low", "CandleLow")
    candle_close = g("candle_close", "CandleClose", "TriggerPrice")

    day_open_at_alert = g("day_open_at_alert", "DayOpen_AtAlert")
    day_high_at_alert = g("day_high_at_alert", "DayHigh_AtAlert")
    day_low_at_alert = g("day_low_at_alert", "DayLow_AtAlert")
    day_close_at_alert = g("day_close_at_alert", "DayClose_AtAlert")

    day_open_final = g("day_open_final", "DayOpen_Final")
    day_high_final = g("day_high_final", "DayHigh_Final")
    day_low_final = g("day_low_final", "DayLow_Final")
    day_close_final = g("day_close_final", "DayClose_Final")
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

    ema20_cross_lookback_td = g('ema20_cross_lookback_td','Ema20CrossLookbackTD')
    ema20_cross_count_total = g('ema20_cross_count_total','Ema20CrossCountTotal')
    ema20_cross_count_bull = g('ema20_cross_count_bull','Ema20CrossCountBull')
    ema20_cross_count_bear = g('ema20_cross_count_bear','Ema20CrossCountBear')
    ema20_cross_days_since_last = g('ema20_cross_days_since_last','Ema20CrossDaysSinceLast')
    ema20_cross_density = g('ema20_cross_density','Ema20CrossDensity')

    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO alerts_log(
                symbol, event_date, event_time, signal, source,
                cross_date, cross_direction,
                close, ema20, ema20_h, ema20_l,
                candle_time,
                candle_open, candle_high, candle_low, candle_close,
                day_open_at_alert, day_high_at_alert, day_low_at_alert, day_close_at_alert,
                day_open_final, day_high_final, day_low_final, day_close_final,
                window_days_primary, window_high_primary, window_low_primary,
                window_days_secondary, window_high_secondary, window_low_secondary,
                break_pct_primary, break_pct_secondary, ema_dist,
                ema20_cross_lookback_td, ema20_cross_count_total, ema20_cross_count_bull,
                ema20_cross_count_bear, ema20_cross_days_since_last, ema20_cross_density
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,? ,?);
            """,
            (
                symbol, event_date, event_time, signal, source,
                cross_date, cross_direction,
                close, ema20, ema20_h, ema20_l,
                candle_time,
                candle_open, candle_high, candle_low, candle_close,
                day_open_at_alert, day_high_at_alert, day_low_at_alert, day_close_at_alert,
                day_open_final, day_high_final, day_low_final, day_close_final,
                d1, window_high_primary, window_low_primary,
                d2, window_high_secondary, window_low_secondary,
                break_pct_primary, break_pct_secondary, ema_dist,
                ema20_cross_lookback_td, ema20_cross_count_total, ema20_cross_count_bull, ema20_cross_count_bear,
                ema20_cross_days_since_last, ema20_cross_density,
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
          candle_open as CandleOpen,
          candle_high as CandleHigh,
          candle_low as CandleLow,
          candle_close as CandleClose,

          day_open_at_alert as DayOpen_AtAlert,
          day_high_at_alert as DayHigh_AtAlert,
          day_low_at_alert as DayLow_AtAlert,
          day_close_at_alert as DayClose_AtAlert,

          day_open_final as DayOpen_Final,
          day_high_final as DayHigh_Final,
          day_low_final as DayLow_Final,
          day_close_final as DayClose_Final,
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
          ema_dist as EmaDist,

          -- EMA20 cross-count features (configured lookback lives in Ema20CrossLookbackTD)
          ema20_cross_lookback_td as Ema20CrossLookbackTD,
          ema20_cross_count_total as Ema20CrossCountTotal,
          ema20_cross_count_bull as Ema20CrossCountBull,
          ema20_cross_count_bear as Ema20CrossCountBear,
          ema20_cross_days_since_last as Ema20CrossDaysSinceLast,
          ema20_cross_density as Ema20CrossDensity
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


def finalize_alerts_day_ohlc(conn: sqlite3.Connection, event_date: str) -> int:
    """Fill *_final day OHLC columns in alerts_log using daily_bars for event_date.

    Returns the number of rows updated.
    """
    conn.execute(
        """
        UPDATE alerts_log
        SET
          day_open_final = (
            SELECT db.open FROM daily_bars db
            WHERE db.symbol = alerts_log.symbol AND db.date = alerts_log.event_date
          ),
          day_high_final = (
            SELECT db.high FROM daily_bars db
            WHERE db.symbol = alerts_log.symbol AND db.date = alerts_log.event_date
          ),
          day_low_final = (
            SELECT db.low FROM daily_bars db
            WHERE db.symbol = alerts_log.symbol AND db.date = alerts_log.event_date
          ),
          day_close_final = (
            SELECT db.close FROM daily_bars db
            WHERE db.symbol = alerts_log.symbol AND db.date = alerts_log.event_date
          )
        WHERE event_date = ?
          AND (day_open_final IS NULL OR day_high_final IS NULL OR day_low_final IS NULL OR day_close_final IS NULL);
        """,
        (event_date,),
    )
    conn.commit()
    cur = conn.execute("SELECT changes();")
    return int(cur.fetchone()[0])


if __name__ == "__main__":
    raise SystemExit(
        "Do not run sqlite_store.py directly. Import and call connect_db/init_db/init_state_tables/init_alerts_log."
    )


# =============================================================
# EOD Scan Alerts (stored in a separate SQLite DB file)
# =============================================================

def init_eod_scan_alerts(conn: sqlite3.Connection) -> None:
    """Create the eod_scan_alerts table if missing."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eod_scan_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            event_date TEXT NOT NULL,   -- YYYY-MM-DD (as-of date)
            event_time TEXT,            -- HH:MM:SS local (scan run time)
            signal TEXT NOT NULL,       -- LONG/SHORT
            -- Anchor
            cross_date TEXT,
            cross_direction TEXT,
            -- Market context (daily)
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            ema20 REAL,
            ema20_h REAL,
            ema20_l REAL,
            candle_time TEXT,           -- daily candle close timestamp (Chicago)
            -- Frozen windows
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
            -- EMA20 cross stats (feature engineering)
            ema20_cross_lookback_td INTEGER,
            ema20_cross_count_total INTEGER,
            ema20_cross_count_bull INTEGER,
            ema20_cross_count_bear INTEGER,
            ema20_cross_days_since_last INTEGER,
            ema20_cross_density REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(symbol, event_date, signal, cross_date)
        );
        """
    )
    conn.commit()


def insert_eod_scan_alert(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    """Insert (or ignore) a single EOD scan alert row."""
    conn.execute(
        """
        INSERT OR IGNORE INTO eod_scan_alerts (
            symbol, event_date, event_time, signal,
            cross_date, cross_direction,
            open, high, low, close, ema20, ema20_h, ema20_l, candle_time,
            window_days_primary, window_high_primary, window_low_primary,
            window_days_secondary, window_high_secondary, window_low_secondary,
            break_pct_primary, break_pct_secondary, ema_dist,
            ema20_cross_lookback_td, ema20_cross_count_total, ema20_cross_count_bull, ema20_cross_count_bear,
            ema20_cross_days_since_last, ema20_cross_density
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row.get("symbol"),
            row.get("event_date"),
            row.get("event_time"),
            row.get("signal"),
            row.get("cross_date"),
            row.get("cross_direction"),
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("ema20"),
            row.get("ema20_h"),
            row.get("ema20_l"),
            row.get("candle_time"),
            row.get("window_days_primary"),
            row.get("window_high_primary"),
            row.get("window_low_primary"),
            row.get("window_days_secondary"),
            row.get("window_high_secondary"),
            row.get("window_low_secondary"),
            row.get("break_pct_primary"),
            row.get("break_pct_secondary"),
            row.get("ema_dist"),
            row.get("ema20_cross_lookback_td"),
            row.get("ema20_cross_count_total"),
            row.get("ema20_cross_count_bull"),
            row.get("ema20_cross_count_bear"),
            row.get("ema20_cross_days_since_last"),
            row.get("ema20_cross_density"),
        ),
    )
    conn.commit()


def read_eod_scan_alerts_for_date(conn: sqlite3.Connection, event_date: str) -> pd.DataFrame:
    """Return EOD scan alerts for a date as a pandas DataFrame."""
    q = """
        SELECT
            symbol AS Symbol,
            signal AS Signal,
            'EOD' AS Source,
            event_date AS EventDate,
            candle_time AS CandleTime,
            event_time AS EventTime,

            open AS CandleOpen,
            high AS CandleHigh,
            low AS CandleLow,
            close AS CandleClose,

            close AS TodayClose,
            ema20 AS EMA20,
            ema20_h AS EMA20_H,
            ema20_l AS EMA20_L,

            window_days_primary AS PrimaryWindowDaysUsed,
            window_high_primary AS WindowHigh_Primary_preCross,
            window_low_primary AS WindowLow_Primary_preCross,
            window_days_secondary AS SecondaryWindowDaysUsed,
            window_high_secondary AS WindowHigh_Secondary_preCross,
            window_low_secondary AS WindowLow_Secondary_preCross,

            break_pct_primary AS BreakPct_Primary,
            break_pct_secondary AS BreakPct_Secondary,
            ema_dist AS EmaDist,
            ema20_cross_lookback_td AS Ema20CrossLookbackTD,
            ema20_cross_count_total AS Ema20CrossCountTotal,
            ema20_cross_count_bull AS Ema20CrossCountBull,
            ema20_cross_count_bear AS Ema20CrossCountBear,
            ema20_cross_days_since_last AS Ema20CrossDaysSinceLast,
            ema20_cross_density AS Ema20CrossDensity,
            cross_date AS LatestCrossDate,
            cross_direction AS LatestCrossDirection
        FROM eod_scan_alerts
        WHERE event_date=?
        ORDER BY symbol
    """
    return pd.read_sql_query(q, conn, params=(event_date,))
