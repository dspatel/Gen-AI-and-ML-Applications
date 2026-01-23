-- ============================
-- EMA20 Scanner - SQLite Checks
-- ============================

.headers on
.mode column

-- 1) List tables
.print "=== Tables ==="
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- 2) Schema: symbol_state
.print "\n=== symbol_state schema ==="
PRAGMA table_info(symbol_state);

-- 3) Schema: alerts_log
.print "\n=== alerts_log schema ==="
PRAGMA table_info(alerts_log);

-- 4) daily_bars date range
.print "\n=== daily_bars date range ==="
SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS rows
FROM daily_bars;

-- 5) symbol_state sample
.print "\n=== symbol_state sample (latest cross) ==="
SELECT
  symbol,
  last_cross_date AS cross_date,
  last_cross_direction AS cross_direction,
  window_days_primary,
  window_high_primary,
  window_low_primary,
  window_days_secondary,
  window_high_secondary,
  window_low_secondary,
  armed,
  last_alert_date
FROM symbol_state
WHERE last_cross_date IS NOT NULL
ORDER BY last_cross_date DESC, symbol
LIMIT 25;

-- 6) Missing window fields count
.print "\n=== Missing window fields count ==="
SELECT
  SUM(CASE WHEN window_days_primary IS NULL THEN 1 ELSE 0 END) AS missing_days_primary,
  SUM(CASE WHEN window_high_primary IS NULL THEN 1 ELSE 0 END) AS missing_high_primary,
  SUM(CASE WHEN window_low_primary  IS NULL THEN 1 ELSE 0 END) AS missing_low_primary,
  SUM(CASE WHEN window_days_secondary IS NULL THEN 1 ELSE 0 END) AS missing_days_secondary,
  SUM(CASE WHEN window_high_secondary IS NULL THEN 1 ELSE 0 END) AS missing_high_secondary,
  SUM(CASE WHEN window_low_secondary  IS NULL THEN 1 ELSE 0 END) AS missing_low_secondary
FROM symbol_state;

-- 7) Alerts for a specific date
.print "\n=== Alerts for a specific date (EDIT DATE BELOW) ==="
SELECT
  event_date,
  symbol,
  signal,
  source,
  candle_time,
  cross_date,
  window_days_primary,
  window_high_primary,
  window_low_primary,
  ema20,
  ema20_h,
  ema20_l,
  created_at
FROM alerts_log
WHERE event_date = '2026-01-13'
ORDER BY created_at ASC;

-- 8) Potential duplicates
.print "\n=== Potential duplicates by key (should be empty) ==="
SELECT
  event_date, symbol, signal, cross_date,
  COUNT(*) AS cnt
FROM alerts_log
GROUP BY event_date, symbol, signal, cross_date
HAVING COUNT(*) > 1
ORDER BY cnt DESC;

-- 9) Count by source
.print "\n=== Alerts counts by source ==="
SELECT source, COUNT(*) AS cnt
FROM alerts_log
GROUP BY source
ORDER BY cnt DESC;
