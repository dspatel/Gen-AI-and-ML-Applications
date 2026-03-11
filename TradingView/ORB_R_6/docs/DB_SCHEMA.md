# Database Schema (SQLite)

## candles
Primary key: (symbol, interval, open_ts_utc)

- symbol, interval
- open_ts_utc, close_ts_utc (ISO8601 TEXT)
- open_ts_cst, close_ts_cst (ISO8601 TEXT with America/Chicago offset)
- cst_date (YYYY-MM-DD TEXT)
- open, high, low, close, volume
- source, ingested_at_cst


## opening_ranges
Primary key: (symbol, session_date_cst, or_minutes)

Identity:
- symbol (TEXT)
- session_date_cst (TEXT 'YYYY-MM-DD')
- or_minutes (INTEGER)
- interval (TEXT)

OR core:
- or_start_ts_cst (TEXT)
- or_end_ts_cst (TEXT)
- or_open, or_close, or_high, or_low, or_mid, or_range (REAL)
- or_volume (REAL)
- or_num_bars (INTEGER)

Session stats (RTH):
- session_open, session_close, session_high, session_low, session_range (REAL)
- session_volume (REAL)

Integrity:
- expected_or_bars, missing_or_bars, is_or_complete (INTEGER)
- expected_session_bars, missing_session_bars, is_session_complete (INTEGER)
- computed_at_cst (TEXT)


## daily_reference_metrics
Primary key: (symbol, asof_date_cst, horizon_days, orb_minutes, interval, include_today_or)

Stores RR values plus supporting metrics for the OR set used to compute RR (composition, overlap, drift, behavior, dominance). See docs/REFERENCE_RANGE.md.
