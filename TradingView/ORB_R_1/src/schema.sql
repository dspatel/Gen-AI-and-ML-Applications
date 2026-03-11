PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS daily_or (
  session_date TEXT NOT NULL,          -- YYYY-MM-DD (exchange session date)
  symbol TEXT NOT NULL,
  or_start TEXT NOT NULL,              -- ISO8601 with offset or naive in market tz
  or_end   TEXT NOT NULL,
  or_high  REAL,
  or_low   REAL,
  or_width REAL,
  interval TEXT NOT NULL,
  orb_minutes INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'computed_from_intraday',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (session_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_daily_or_symbol_date ON daily_or(symbol, session_date);

CREATE TABLE IF NOT EXISTS daily_reference_metrics (
  asof_date TEXT NOT NULL,             -- date we compute RR for (YYYY-MM-DD)
  symbol TEXT NOT NULL,
  horizon_days INTEGER NOT NULL,

  ref_high  REAL,
  ref_low   REAL,
  ref_width REAL,

  -- OR overlap stats across lookback OR bands (computed from available OR days)
  pairs_total INTEGER,
  or_overlap_pairs_count INTEGER,
  or_overlap_pairs_pct REAL,
  or_overlap_days_count INTEGER,
  or_overlap_days_pct REAL,
  or_overlap_ratio REAL,

  -- Behavior metrics (relative to each day's OWN OR; aggregated across available days)
  median_inside_own_or_pct REAL,
  median_range_to_or REAL,
  mean_direction_bias REAL,
  bias_consistency REAL,
  median_or_width REAL,
  inflation_factor REAL,

  -- Behavior metric audit (intraday availability may differ from OR availability)
  behavior_days_required INTEGER NOT NULL DEFAULT 0,
  behavior_days_available INTEGER NOT NULL DEFAULT 0,
  behavior_days_missing_json TEXT NOT NULL DEFAULT '[]',
  behavior_failure_reason TEXT NOT NULL DEFAULT '',

  -- evaluation / audit (vNext)
  required_days INTEGER NOT NULL,
  available_days INTEGER NOT NULL,
  coverage_ratio REAL NOT NULL,
  is_valid INTEGER NOT NULL,           -- 0/1
  missing_or_dates_json TEXT NOT NULL, -- JSON list of YYYY-MM-DD
  failure_reason TEXT NOT NULL,
  used_today_or INTEGER NOT NULL,      -- 0/1
  today_or_ready INTEGER NOT NULL,     -- 0/1 at compute time

  interval TEXT NOT NULL,
  orb_minutes INTEGER NOT NULL,
  include_today_or INTEGER NOT NULL,

  created_at TEXT NOT NULL DEFAULT (datetime('now')),

  PRIMARY KEY (asof_date, symbol, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_rr_symbol_asof ON daily_reference_metrics(symbol, asof_date);


-- -----------------------------
-- Breakout events (alerts)
-- -----------------------------
CREATE TABLE IF NOT EXISTS breakout_events (
  event_id TEXT NOT NULL,              -- uuid-like string
  asof_date TEXT NOT NULL,             -- YYYY-MM-DD (session date)
  symbol TEXT NOT NULL,
  timestamp TEXT NOT NULL,             -- ISO8601 timestamp of triggering bar (market tz)
  direction TEXT NOT NULL,             -- 'UP' or 'DOWN'
  primary_horizon INTEGER NOT NULL,
  also_horizons_json TEXT NOT NULL DEFAULT '[]',
  close REAL,
  ref_high REAL,
  ref_low REAL,
  ref_width REAL,
  breakout_amt REAL,
  breakout_strength REAL,
  close_pen REAL,
  wick_pen REAL,
  body_norm REAL,
  range_norm REAL,
  decision TEXT,
  confidence REAL,
  confidence_pct INTEGER,
  labels_json TEXT NOT NULL DEFAULT '{}',
  message TEXT NOT NULL DEFAULT '',
  is_replay INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (event_id)
);

CREATE INDEX IF NOT EXISTS idx_breakout_events_date_sym ON breakout_events(asof_date, symbol);

CREATE TABLE IF NOT EXISTS event_horizon_metrics (
  event_id TEXT NOT NULL,
  horizon_days INTEGER NOT NULL,
  did_break INTEGER NOT NULL,          -- 0/1
  break_rank INTEGER,                  -- 1..k if did_break else NULL
  ref_high REAL,
  ref_low REAL,
  ref_width REAL,
  breakout_amt REAL,
  breakout_strength REAL,
  close_pen REAL,
  wick_pen REAL,
  body_norm REAL,
  range_norm REAL,

  -- copy a few key RR stats for that horizon (optional but useful)
  or_overlap_pairs_pct REAL,
  median_inside_own_or_pct REAL,
  median_range_to_or REAL,
  mean_direction_bias REAL,
  bias_consistency REAL,
  inflation_factor REAL,

  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (event_id, horizon_days),
  FOREIGN KEY (event_id) REFERENCES breakout_events(event_id) ON DELETE CASCADE
);

