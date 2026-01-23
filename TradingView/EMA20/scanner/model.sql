-- EMA20 Anchored Breakout Scanner
-- SQLite schema reference (authoritative documentation)
--
-- NOTE
--   * The application initializes/migrates this schema at runtime (utils/sqlite_store.py).
--   * Keep this file updated when schema changes (see DEVELOPMENT_RULES.md).

PRAGMA foreign_keys = ON;

-- =============================================================
-- 1) daily_bars
-- Purpose: Cache DAILY OHLCV candles and EMA20 metrics
-- =============================================================
CREATE TABLE IF NOT EXISTS daily_bars (
  symbol      TEXT NOT NULL,
  date        TEXT NOT NULL,          -- YYYY-MM-DD
  open        REAL,
  high        REAL,
  low         REAL,
  close       REAL,
  volume      REAL,

  -- Derived indicators (daily)
  ema20       REAL,
  ema20_h     REAL,
  ema20_l     REAL,

  PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date
  ON daily_bars(symbol, date);

-- =============================================================
-- 2) symbol_state
-- Purpose: Per-symbol state machine for anchored windows + arming
-- =============================================================
CREATE TABLE IF NOT EXISTS symbol_state (
  symbol               TEXT PRIMARY KEY,

  -- Latest EMA20 range-cross info used as the anchor
  last_cross_date      TEXT,          -- YYYY-MM-DD
  last_cross_direction TEXT,          -- UP / DOWN (as computed by project logic)

  -- Frozen windows anchored to last_cross_date (computed from PRE-cross candles)
  window_high_7        REAL,
  window_low_7         REAL,
  window_high_21       REAL,
  window_low_21        REAL,

  -- State machine flags
  armed                INTEGER DEFAULT 1,   -- 1=armed, 0=disarmed

  -- Book-keeping
  last_alert_date      TEXT,          -- YYYY-MM-DD (when an alert last fired)
  last_alert_signal    TEXT           -- LONG / SHORT
);

-- =============================================================
-- 3) alerts_log
-- Purpose: Durable ledger of alerts (LIVE + EOD) with de-duplication
-- =============================================================
CREATE TABLE IF NOT EXISTS alerts_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol          TEXT NOT NULL,
  event_date      TEXT NOT NULL,     -- YYYY-MM-DD
  event_time      TEXT,              -- HH:MM:SS (optional for LIVE)
  signal          TEXT NOT NULL,      -- LONG / SHORT
  source          TEXT NOT NULL,      -- LIVE / EOD / BACKTEST

  -- Context (useful for audit/debug)
  latest_cross_date      TEXT,
  latest_cross_direction TEXT,
  window_high_7          REAL,
  window_low_7           REAL,
  window_high_21         REAL,
  window_low_21          REAL,
  ema20                  REAL,
  ema20_h                REAL,
  ema20_l                REAL,
  today_close            REAL,
  break_pct_7d           REAL,
  break_pct_21d          REAL,
  ema_distance           REAL,

  created_at      TEXT DEFAULT (datetime('now'))
);

-- De-dupe key: same symbol + day + signal + anchor cross should only be recorded once
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_log_dedupe
  ON alerts_log(symbol, event_date, signal, latest_cross_date);

CREATE INDEX IF NOT EXISTS idx_alerts_log_event_date
  ON alerts_log(event_date);
