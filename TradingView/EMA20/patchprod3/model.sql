-- EMA20 Anchored Breakout Scanner
-- SQLite schema reference (authoritative documentation)
--
-- NOTE:
--   This project uses a "nuke DB" workflow when the schema changes.
--   If you update this file, delete the existing DB file so init_* creates the new schema.

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

  -- Latest EMA20 cross info used as the anchor
  last_cross_date      TEXT,          -- YYYY-MM-DD
  last_cross_direction TEXT,          -- UP / DOWN

  -- Frozen windows anchored to last_cross_date (computed from PRE-cross candles)
  window_days_primary      INTEGER,
  window_high_primary      REAL,
  window_low_primary       REAL,

  window_days_secondary    INTEGER,
  window_high_secondary    REAL,
  window_low_secondary     REAL,

  -- State machine flags
  armed                INTEGER DEFAULT 1,   -- 1=armed, 0=disarmed

  -- Book-keeping
  last_alert_date      TEXT,          -- YYYY-MM-DD
  last_alert_signal    TEXT,          -- LONG / SHORT

  updated_at           TEXT DEFAULT (datetime('now'))
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

  -- Anchor
  cross_date            TEXT,
  cross_direction       TEXT,

  -- Context (useful for audit/debug)
  candle_time           TEXT,
  -- Trigger candle (intraday) OHLC at alert time
  candle_open           REAL,
  candle_high           REAL,
  candle_low            REAL,
  candle_close          REAL,

  -- Day (RTH session) OHLC as known at alert time
  day_open_at_alert     REAL,
  day_high_at_alert     REAL,
  day_low_at_alert      REAL,
  day_close_at_alert    REAL,

  -- Final daily OHLC after market close (filled during EOD finalization)
  day_open_final        REAL,
  day_high_final        REAL,
  day_low_final         REAL,
  day_close_final       REAL,
  close                 REAL,
  ema20                 REAL,
  ema20_h               REAL,
  ema20_l               REAL,

  window_days_primary      INTEGER,
  window_high_primary      REAL,
  window_low_primary       REAL,

  window_days_secondary    INTEGER,
  window_high_secondary    REAL,
  window_low_secondary     REAL,

  break_pct_primary     REAL,
  break_pct_secondary   REAL,
  ema_dist              REAL,

  -- EMA20 cross stats (feature engineering)
  ema20_cross_lookback_td     INTEGER,
  ema20_cross_count_total     INTEGER,
  ema20_cross_count_bull      INTEGER,
  ema20_cross_count_bear      INTEGER,
  ema20_cross_days_since_last INTEGER,
  ema20_cross_density         REAL,

  created_at      TEXT DEFAULT (datetime('now'))
);

-- De-dupe key: same symbol + day + signal + anchor cross should only be recorded once
CREATE UNIQUE INDEX IF NOT EXISTS uq_alerts_log_dedupe
  ON alerts_log(symbol, event_date, signal, cross_date);

CREATE INDEX IF NOT EXISTS idx_alerts_log_event_date
  ON alerts_log(event_date);
