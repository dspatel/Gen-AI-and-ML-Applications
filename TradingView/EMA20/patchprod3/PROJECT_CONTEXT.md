# Project Context — EMA20 Anchored Breakout Scanner

## What this project does
This project scans a universe of US stocks (from TradingView screener exports) and:

1) Uses **Yahoo Finance daily candles** to identify the most recent **EMA20 “touch/cross”** within the last N trading days.
2) Anchors (freezes) pre-cross windows (**7D** and **21D**) that remain fixed until the next cross.
3) Generates **EOD scan outputs** and optional Discord summary/table.
4) Runs a **live tracker** (yfinance intraday) that fires Discord alerts immediately when conditions are met.
5) Uses a SQLite **alerts ledger** to dedupe LIVE + EOD alerts and prevent re-run wipeouts.

The user’s timezone is **America/Chicago**.

---

## Key design choices

### Single source of truth
- **Daily cached data** lives in SQLite (`daily_bars`).
- **Per-symbol frozen-window state** lives in SQLite (`symbol_state`).
- **Alerts ledger** lives in SQLite (`alerts_log`).

`alerts_log` now also stores **trigger candle OHLC** (LIVE), **day OHLC at alert time**, and **final daily OHLC** (filled after close). Step 3 exports `scan_alerts_YYYY-MM-DD.csv` from this ledger so it matches what fired live.

EOD outputs are derived from the DB, and LIVE uses the same DB state.

### Frozen window philosophy
- When a new cross is detected, the 7D/secondary window (configurable)s are frozen using *only* the candles **before** CrossDate.
- Window values are not recomputed daily; they change only when CrossDate changes.

### Alert philosophy
- The project is designed to reduce noise:
  - Alerts fire only when a symbol is **armed**.
  - After firing, a symbol disarms.
  - With `REARM_ON_REENTRY`, it rearms only after price re-enters the primary window (configurable, e.g., 35D).

---

## Run modes

### EOD mode (after close)
- Run `run_step3_scan_from_sqlite.py`.
- Generates CSV outputs.
- Optionally sends Discord summary/table.

### Live mode (during session)
- Run `run_live_tracker_yf.py`.
- Waits for the configured session start (pre / regular / post / all).
- Polls yfinance intraday data.
- Inserts alerts into the ledger and optionally sends Discord alerts.

---

## Files that must be updated when changing behavior
- `config.py` — toggles and defaults
- `utils/sqlite_store.py` — schema/migrations/ledger
- `model.sql` — schema reference (must match)
- `README.md` + `USER_GUIDE.md` — strategy + operational documentation
- `CHANGELOG.md` — version notes

---

## Intended future extensions
- Add scoring/ranking using 7D/21D alignment.
- Add ATR-based risk sizing.
- Add multi-timeframe confirmation rules.
- Add broker or TradingView feed as optional live source.

### Daily orchestration

Use:

```bash
python daily_runner.py --mode daily
```

The runner computes NYSE open/close in America/Chicago and uses environment-variable overrides so Step 3 can run as-of the last trading day for morning prep without editing `config.py`.


### 2026-01 Update: Separate EOD Scan DB

- EOD scan alerts are no longer inserted into `alerts_log`.
- Step3 writes EOD alerts to a separate SQLite file (`CFG.EOD_DB_PATH`) and exports `eod_scan_alerts_<date>.csv`.
- LIVE intraday alerts remain in the production DB (`alerts_log`) and Step3 exports `live_alerts_<date>.csv`.
- Discord EOD message attaches both CSVs.


- Added EMA20 cross-count feature: compute recent EMA20 range-cross events over configurable trading-day lookback and store in both LIVE and EOD outputs.
