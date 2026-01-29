# Changelog — EMA20 Anchored Breakout Scanner

All notable changes to this project are documented here.

## [0.3.0] — 2026-01-13
### Added
- Live tracker (`run_live_tracker_yf.py`) using yfinance intraday data.
- NYSE trading day + open/close detection via `exchange-calendars` (XNYS).
- Live session mode controls (`LIVE_SESSION_MODE`, `LIVE_PREMARKET_START`, `LIVE_POSTMARKET_END`).
- Alerts ledger (`alerts_log`) with de-dupe across LIVE and EOD runs.
- Safeguard to prevent overwriting a non-empty alerts file with an empty run (`PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY`).
- Schema reference file `model.sql` documenting `daily_bars`, `symbol_state`, and `alerts_log`.

### Changed
- EOD outputs include: `EMA20`, `EMA20_H`, `EMA20_L`, and frozen 7D + secondary window (configurable)s.
- Backtest default set to off (`BACKTEST_MODE=False`).

## [0.2.0] — 2026-01-12
### Added
- Long window anchoring (21D) in addition to 7D.
- Cross-universe persistence (`ema20_cross_YYYY-MM-DD.csv`).
- Discord notification module (webhook) with toggles.

## [0.1.0] — 2026-01-11
### Added
- Step1 TradingView screener export ingestion.
- Step2 Yahoo daily candle ingestion to SQLite and EMA20 calculation.
- Step3 EOD scan with armed/rearm logic and CSV outputs.


## Unreleased
- Reordered `scan_alerts_YYYY-MM-DD.csv` columns for readability (no fields dropped).

### Added
- **Alerts ledger enrichment for simulation**: LIVE alerts now persist the trigger candle OHLC and the in-session (RTH) day OHLC-at-alert. EOD scan fills final daily OHLC for the alert day.
- **scan_alerts CSV now comes from SQLite `alerts_log`** (durable ledger), so it always reflects the *actual* trigger price/time captured live.

### Changed
- Step 3 EOD scan now runs a small finalization pass to populate `Day*_Final` columns in the alerts ledger using `daily_bars`.
- Added configurable primary/secondary frozen window lengths via `CFG.WINDOW_DAYS_PRIMARY` and `CFG.WINDOW_DAYS_SECONDARY`.
- Outputs and Discord alerts now label windows with the exact day counts (e.g., `WindowHigh_35D_preCross`).

## 2026-01-21
- Added `daily_runner.py --mode daily` to orchestrate morning prep → live → EOD automatically using NYSE session times in America/Chicago.


## Unreleased
- Separate EOD scan database (`EOD_DB_PATH`) to keep LIVE alerts ledger clean.
- Step3 now produces two CSVs and attaches both to Discord (EOD scan + LIVE ledger).
- Daily runner prints explicit EOD Step2->Step3 transition.

- **New tool:** `python tools/print_runtime_config.py` prints effective runtime config (DB paths, outputs, Discord) with secrets masked.


## Unreleased
- Added EMA20 cross-count feature (bull/bear/total, days-since, density) stored in DB/CSV and shown in LIVE Discord alerts.
