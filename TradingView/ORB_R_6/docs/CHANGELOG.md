## v0.10.9
- LIVE: removed any pre-loop `continue`; pre-open waiting now uses sleep without `continue` outside loops.
- Added build marker in LIVE banner.

# Changelog

## v0.10.7
- LIVE: prevent yfinance requests before the first completed candle close (anchor last_complete to session_start + interval).
- LIVE/YF: reduce end-time padding from +1 minute to +1 second to avoid requesting slightly-future windows.


## 0.10.4
- Live: ingest window now ends at the last fully closed candle (now-1min floored), with a +1min buffer, to avoid Yahoo "Data doesn't exist" errors caused by requesting incomplete/future bars.
- Live: continues to compute session windows in CST; yfinance may still internally convert to UTC, but requests now align to completed bars.


## 0.6.3
- Fixed indentation error in replay runner (rewrite src/replay.py) after breakout_events schema expansion.

## 0.6.2
- Expanded breakout_events to store RR snapshot at trigger time, include_today_or flag, interval/orb_minutes, and triggering candle OHLC + bar open/close timestamps.
- Added lightweight migration logic (ALTER TABLE) in ensure_breakout_tables for existing DBs.
- Added docs/SCHEMA.md documenting breakout_events.

## 0.6.1
- Fixed indentation error in replay runner by rewriting src/replay.py cleanly.

## 0.6.0
- Simplified config: one canonical `asof_date_cst` used by src.run and src.replay.
- Implemented two RR variants per as-of date: pre-OR (include_today_or=0) and post-OR (include_today_or=1), stored in daily_reference_metrics.
- Replay now switches RR intraday: prior-only until OR window completes, then includes today's OR additively.

## 0.5.5
- Fixed indentation error in pipeline by rewriting src/pipeline.py cleanly (keeps as-of anchoring behavior).

## 0.5.4
- Added production-grade as-of anchoring: `run.asof_date_cst` controls the computation window for src.run (blank -> current session).
- Replay now DB-first backfills candles/OR/RR for `replay.asof_date_cst` automatically (ensure_asof_ready).
- Updated docs/USAGE.md with backtest anchor behavior.

## 0.5.3
- Replay robustness: ensure `daily_reference_metrics` table exists before querying (prevents 'no such table' errors when DB is new/mismatched).

## 0.5.2
- Fixed replay runner to resolve symbols using symbols loader (SymbolsConfig -> list of tickers).

## 0.5.1
- Fixed config parsing for replay/discord sections by adding passthrough fields to AppConfig (replay now reads replay.asof_date_cst correctly).

## 0.5.0
- Added close-only breakout detection with per-horizon re-arming.
- Added normalized storage: breakout_events + event_horizon_metrics.
- Added replay runner (bar-by-bar) using stored candles + RR metrics.
- Added Discord notifier with editable YAML templates.
- Added docs/USAGE.md.

## 0.4.2
- Restored full Step 1 ingestion pipeline and fixed AppConfig usage (db_path, symbols, window construction).
- Step 3 RR metrics now runs after Step 2 without relying on non-existent cfg.db_conn/reference fields.

## 0.4.1
- Fixed indentation error in pipeline run_from_config (Step 3 block).

## 0.4.0
- Added multi-horizon Reference Range computation and `daily_reference_metrics` table with full supporting metrics (composition, overlap, drift, behavior, dominance).
- Added docs/REFERENCE_RANGE.md defining all stored metrics.

## 0.3.1
- Fixed indentation error in pipeline Step 2 block.

## 0.3.0
- Added daily `opening_ranges` table (30-minute OR) with session stats + integrity fields.
- Pipeline now computes ORs after candle ingestion and upserts them into SQLite.
- Added OR computation module + updated DB schema docs.

## 0.2.1
- Fixed CST timestamp formatting: pandas DatetimeIndex has no `.isoformat()`. Now uses ISO-8601 strings with `T` and `±HH:MM` offset.

## 0.2.0
- Renamed database to orb_core.sqlite
- Moved DB into structured folder: data/db/
- Prepared structure for multi-table architecture

# Changelog

## 0.6.3
- Fixed indentation error in replay runner (rewrite src/replay.py) after breakout_events schema expansion.

## 0.6.2
- Expanded breakout_events to store RR snapshot at trigger time, include_today_or flag, interval/orb_minutes, and triggering candle OHLC + bar open/close timestamps.
- Added lightweight migration logic (ALTER TABLE) in ensure_breakout_tables for existing DBs.
- Added docs/SCHEMA.md documenting breakout_events.

## 0.6.1
- Fixed indentation error in replay runner by rewriting src/replay.py cleanly.

## 0.6.0
- Simplified config: one canonical `asof_date_cst` used by src.run and src.replay.
- Implemented two RR variants per as-of date: pre-OR (include_today_or=0) and post-OR (include_today_or=1), stored in daily_reference_metrics.
- Replay now switches RR intraday: prior-only until OR window completes, then includes today's OR additively.

## 0.5.5
- Fixed indentation error in pipeline by rewriting src/pipeline.py cleanly (keeps as-of anchoring behavior).

## 0.5.4
- Added production-grade as-of anchoring: `run.asof_date_cst` controls the computation window for src.run (blank -> current session).
- Replay now DB-first backfills candles/OR/RR for `replay.asof_date_cst` automatically (ensure_asof_ready).
- Updated docs/USAGE.md with backtest anchor behavior.

## 0.5.3
- Replay robustness: ensure `daily_reference_metrics` table exists before querying (prevents 'no such table' errors when DB is new/mismatched).

## 0.5.2
- Fixed replay runner to resolve symbols using symbols loader (SymbolsConfig -> list of tickers).

## 0.5.1
- Fixed config parsing for replay/discord sections by adding passthrough fields to AppConfig (replay now reads replay.asof_date_cst correctly).

## 0.5.0
- Added close-only breakout detection with per-horizon re-arming.
- Added normalized storage: breakout_events + event_horizon_metrics.
- Added replay runner (bar-by-bar) using stored candles + RR metrics.
- Added Discord notifier with editable YAML templates.
- Added docs/USAGE.md.

## 0.4.2
- Restored full Step 1 ingestion pipeline and fixed AppConfig usage (db_path, symbols, window construction).
- Step 3 RR metrics now runs after Step 2 without relying on non-existent cfg.db_conn/reference fields.

## 0.4.1
- Fixed indentation error in pipeline run_from_config (Step 3 block).

## 0.4.0
- Added multi-horizon Reference Range computation and `daily_reference_metrics` table with full supporting metrics (composition, overlap, drift, behavior, dominance).
- Added docs/REFERENCE_RANGE.md defining all stored metrics.

## 0.3.1
- Fixed indentation error in pipeline Step 2 block.

## 0.3.0
- Added daily `opening_ranges` table (30-minute OR) with session stats + integrity fields.
- Pipeline now computes ORs after candle ingestion and upserts them into SQLite.
- Added OR computation module + updated DB schema docs.

## 0.2.1
- Fixed CST timestamp formatting: pandas DatetimeIndex has no `.isoformat()`. Now uses ISO-8601 strings with `T` and `±HH:MM` offset.

## 0.1.4
- Fixed timezone-aware index conversion when creating open_ts_utc/close_ts_utc strings (prevents pandas TypeError).

## 0.1.3
- Fixed yfinance column normalization when columns are tuples / MultiIndex, preventing:
  AttributeError: 'tuple' object has no attribute 'lower'
- Keeps v0.1.2 fix: pass naive UTC datetimes to yfinance start/end.

## 0.1.2
- Patched yfinance start/end parsing error by passing naive UTC datetimes.

## 0.1.1
- Added config.yaml and config-driven runner.

## 0.7.0
- Added inflation_factor to daily_reference_metrics.
- Added trigger candle quality metrics to event_horizon_metrics.
- Added decision engine v1 stored on breakout_events.
- Updated Discord alert template to readable multi-section format with meaningful emojis/labels.
- Added/updated supporting docs (METRICS, BREAKOUT_METRICS, DECISION_ENGINE, NOTIFICATION_FORMAT).

## 0.7.1
- Fixed compute_reference_metrics SyntaxError (inflation_factor wiring) and ensured upsert uses correct keyword/param ordering.

## 0.7.2
- Fixed NameError in compute_reference_metrics (or_df_ typo).

## 0.7.3
- Fixed lingering NameError in compute_reference_metrics by removing or_df_ references and adding defensive alias.

## 0.7.4
- Fixed NameError in compute_reference_metrics: compute inflation_factor from or_widths list (no or_df dependency).

## 0.7.5
- Fixed SQLite insert mismatch in daily_reference_metrics upsert by using dynamic placeholders from row dict.

## 0.7.6
- Robust daily_reference_metrics upsert now reads SQLite schema via PRAGMA and supplies values for all columns.

## 0.7.7
- Fixed AttributeError in RR upsert: use dataclasses.asdict(row) instead of row.as_dict().

## 0.8.0
- Clean rebuild of src/breakouts.py to fix Python signature ordering, remove syntax errors, and keep schema migrations stable.

## 0.9.0
- Reference Range: fixed include_today_or behavior to be additive (prior H sessions + today OR), with sessions_required = H+1 when include_today_or=1.
- RR now loads OR rows from last max horizon prior sessions plus as-of date when needed.

## 0.9.2
- Refactor: pipeline.run_from_config now delegates DB-first prep to prepare_asof.ensure_asof_ready (single source of truth for candles/OR/RR prep).
- Added src/breakout_engine.py and refactored replay to use shared per-bar breakout processing (prepares for LIVE tracker to share identical logic).

## 0.9.3
- Fixed replay DB candle loader to filter by `candles.cst_date` (schema uses `cst_date`, not `session_date_cst`).

## 0.9.4
- Added `src.live` (production-grade yfinance polling loop) that reuses the shared breakout engine.
- Consolidated breakout “nature” metrics in `quality_metrics.compute_quality` and persisted them in `event_horizon_metrics`:
  `close_pos`, `upper_wick_ratio`, `lower_wick_ratio`, `clean_break` (+ migrations).
- Updated Discord notification template to display clean-break + close-position.

## 0.9.5
- Fixed live tracker config access (AppConfig.db_path).

## 0.9.6
- Fixed SQLite 'unable to open database file' by resolving relative db_path against project root and creating parent directories in db.connect().

## 0.9.8
- Fix: daily_reference_metrics row creation when RR is incomplete now supplies inflation_factor=None (prevents crash in live).

## 0.10.1
- Fixed db.py import crash by moving conn.commit() inside init_db().

## 0.10.2
- Fix: removed stray module-scope conn.commit() in compute_opening_ranges.py that caused NameError during import.
