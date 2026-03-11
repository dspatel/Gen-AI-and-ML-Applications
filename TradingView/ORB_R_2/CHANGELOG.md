
## 1.1.0 — 2026-02-10
- Discord: rewire webhook sender to use requests (robust error handling + debug info); replay no longer crashes on webhook errors.
- Add: tools.test_webhook to validate webhook connectivity and see HTTP status/body.


## 1.0.9 — 2026-02-10
- UX: remove redundant title printing in replay; reorder template so Decision is line 2.
- Safety: Discord webhook send no longer crashes on HTTP errors; logs status/body excerpt.


## 1.0.8 — 2026-02-10
- Fix: include breakout intensity fields in replay/demo notification payload so printed messages match CSV (close_pen, wick_pen, body_norm, range_norm).
- Improvement: Discord notifier now catches HTTP errors and prints response details instead of crashing.


## 1.0.7 — 2026-02-10
- Fix: include breakout intensity fields in replay/demo notification payload (**intensity) so printed/Discord messages match CSV values.


## 1.0.6 — 2026-02-10
- Fix: render replay/demo notifications after intensity fields are merged into payload; intensity line now shows non-zero percentages.
- Clarify: ladder emits events only for horizons that actually break (e.g., 3D and 5D) — if 9D doesn't break, no 9D event.


## 1.0.5 — 2026-02-10
- Fix: include breakout intensity fields in replay and demo_step6 notification payloads (close_pen, wick_pen, body_norm, range_norm).
- Docs: clarify demo_step6 uses config asof_date; replay uses CLI date; Discord sending requires notifications.enabled and replay --send.


## 1.0.4 — 2026-02-10
- Fix: replay_day now includes intensity fields in notification payload (close_pen, wick_pen, body_norm, range_norm).
- Fix: notification rendering is now resilient—defaults are applied so missing fields won't crash templates.


## 1.0.3 — 2026-02-10
- Fix: replay + demo_step6 notifications include intensity fields required by templates (close_pen, wick_pen, etc.).
- Add: OR overlap Option A — adjacent overlap metrics (neighbor sessions) alongside existing all-pairs overlap.
- Storage: migrate v2 tables to include adjacent overlap columns.

# Changelog

## v1.0.1
- Fix data_fetch NameError (spec.interval)
- Normalize OHLC columns after provider fetch
- Add OR overlap day-count (days overlapping with at least one other day) to notifications


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

## v1.0.1
- Fix data_fetch NameError (spec.interval)
- Normalize OHLC columns after provider fetch
- Add OR overlap day-count (days overlapping with at least one other day) to notifications


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
