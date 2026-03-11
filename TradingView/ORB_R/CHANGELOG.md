# Changelog

## v0.6.0 (Step 8)
- Added decision & confidence layer (rule-based, YAML-driven)
- Decision output appended to events CSV and stored in SQLite
- Notification templates can include decision summary
- Updated docs with decision rationale


## v0.5.1 (Step 7 patch)
- Fixed SQLite events upsert schema mismatch via lightweight migrations + column filtering
- Hardened store against extra CSV columns

## v0.5.0 (Step 7)
- Added SQLite storage layer (prod/test separation)
- Upsert daily metrics and breakout events into DB
- Backfill tool to run date ranges into test or prod DB
- Repair tool to delete/rebuild date ranges safely

## v0.4.0 (Step 6)
- Close-only breakout detection over reference range with re-arm logic
- Breakout intensity metrics (close/wick penetration, body normalized)
- Notification rendering via templates
- Day-centric events report
- Added sessions_requested/nonempty/used + missing dates to daily metrics report


# Changelog

## v0.6.0 (Step 8)
- Added decision & confidence layer (rule-based, YAML-driven)
- Decision output appended to events CSV and stored in SQLite
- Notification templates can include decision summary
- Updated docs with decision rationale


## v0.3.1 (combined Step 4 + Step 5)
- Day-centric reporting across all symbols (`reports/daily/<date>_metrics.csv`)
- Universe loader supports YAML + optional txt/csv watchlist
- Daily OR, reference range, and lookback behavior metrics implemented
- Retains Step 4 deterministic session fetch with CSV cache
- TradingSessions compatible with multiple exchange_calendars APIs/column names

## v0.2.1
- Recommended structure: `src/` layout; interpretation under package; cache is runtime-only
