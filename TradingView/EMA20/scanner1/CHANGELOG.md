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
- EOD outputs include: `EMA20`, `EMA20_H`, `EMA20_L`, and frozen 7D + 21D windows.
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
