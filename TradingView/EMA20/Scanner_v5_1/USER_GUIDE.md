# User Guide — EMA20 Anchored Breakout Scanner

This project has two modes:

1. **EOD Scanner (daily candles)** — run after the close to compute the eligible universe, anchored windows, and the day’s alerts.
2. **Live Tracker (intraday yfinance)** — run during the session to fire Discord alerts as soon as conditions are met.

All times are interpreted in **America/Chicago** (CST/CDT automatically), unless noted.

---

## Mental model

**EMA20 touch resets context → freeze pre-cross structure → arm → aligned breakout → alert → disarm → re-enter → re-arm**

---

## Strategy rules (authoritative)

### 1) EMA20 “touch/cross” (daily)
A daily candle is considered a cross/touch if:

- `Low ≤ EMA20 ≤ High`

This matches your requirement: *“crossover can be considered even if the close of the day was not on the other side.”*

Direction recorded:
- **UP** if `Close ≥ EMA20`
- **DOWN** otherwise

Only symbols with a latest cross within the last `CROSS_LOOKBACK_DAYS` trading days (default: 30) remain eligible.

### 2) Frozen windows anchored to CrossDate
When the latest CrossDate is found, we freeze two pre-cross windows (CrossDate excluded):

- **primary window (configurable, e.g., 35D)**
  - `WindowHigh_{WINDOW_DAYS_PRIMARY}D_preCross = max(High)` over the N trading days BEFORE CrossDate
  - `WindowLow_{WINDOW_DAYS_PRIMARY}D_preCross  = min(Low)`  over the N trading days BEFORE CrossDate

 - **secondary window (optional + configurable)**
  - `WindowHigh_{WINDOW_DAYS_SECONDARY}D_preCross = max(High)` over the M trading days BEFORE CrossDate
  - `WindowLow_{WINDOW_DAYS_SECONDARY}D_preCross  = min(Low)`  over the M trading days BEFORE CrossDate

These frozen window values are stored in SQLite (`symbol_state`). They are recomputed when:
- a **new CrossDate** is detected, OR
- you change `WINDOW_DAYS_PRIMARY` / `WINDOW_DAYS_SECONDARY` in `config.py`.

### 3) Alert conditions
Alerts are evaluated using the frozen **primary window** and the EMA20 filter:

**LONG**
- `Price > WindowHigh_{WINDOW_DAYS_PRIMARY}D_preCross` AND `Price > EMA20`

**SHORT**
- `Price < WindowLow_{WINDOW_DAYS_PRIMARY}D_preCross` AND `Price < EMA20`

Where **Price** is:
- EOD scanner: *today’s close*
- Live tracker: *latest intraday price* (and daily EMA20 is computed by adding a synthetic “today” row from intraday high/low/close)

The secondary window (configurable) is included as additional context in outputs and Discord messages.

### No-confusion outputs
All scan CSVs and alert rows explicitly store the window lengths used:
- `PrimaryWindowDaysUsed`
- `SecondaryWindowDaysUsed`

In addition, the window columns themselves are labeled with the day count (e.g., `WindowHigh_30D_preCross`).

### 4) Arming / disarming / re-arming
State machine per symbol:

- New CrossDate → `armed = 1`
- When an alert fires → `armed = 0`
- If `REARM_ON_REENTRY = True`, re-arm when Price re-enters the **primary window ({WINDOW_DAYS_PRIMARY}D)**:
  - strict: `WindowLow < Price < WindowHigh`
  - inclusive: `WindowLow ≤ Price ≤ WindowHigh`

---

## First run sequence (recommended)

If this is your first time running the project (no symbols files exist yet), follow this exact order.

### Step 0 — Install dependencies
```bash
pip install -r requirements.txt
```

### Step 1 — Download TradingView universe (symbols)
This creates today’s universe file:
- `data/symbols/symbols_YYYY-MM-DD.csv`

Run:
```bash
python run_step1_download_tv.py
```

### Step 2 — Fetch daily candles from Yahoo into SQLite
Run anytime pre-market (for example 8:00–8:20am Chicago time):
```bash
python run_step2_fetch_yf_to_sqlite.py
```

### Step 3 (optional but recommended pre-open) — Build the cross-universe file
The crossover rule is based on **completed daily candles**, so pre-open you usually want the *latest completed trading day* as your reference.

- If you run this pre-open, it will build the eligible universe based on yesterday’s daily data.
- It will create:
  - `data/symbols/ema20_cross_YYYY-MM-DD.csv`

Run:
```bash
python run_step3_scan_from_sqlite.py
```

### Step 4 — Start live monitoring (start at ~8:25am)
Run:
```bash
python run_live_tracker_yf.py
```

If started before the configured session start time, it will wait until the session begins.

---

## Daily workflow (normal operations)

### Pre-market
1) Step 1 — download TradingView symbols (optional if you don’t need a refresh every day)
2) Step 2 — update daily bars (recommended daily)
3) Start live tracker (optional)

### After close (EOD)
1) Step 3 — EOD scan; produces outputs and optionally posts Discord summary

---

## Output files

### 1) Scan output (all symbols processed)
- `data/outputs/scan_all_YYYY-MM-DD.csv`

### 2) Alerts output (final truth)
- `data/outputs/scan_alerts_YYYY-MM-DD.csv`

This file is built from the **alerts ledger** (`alerts_log`) so it includes alerts fired by LIVE mode and avoids duplicates.

### 3) Cross-universe file (symbols to monitor further)
- `data/symbols/ema20_cross_YYYY-MM-DD.csv`

---

## Configuration toggles (config.py)

All toggles live in `config.py` under the `Config` dataclass.

### TradingView export (Step 1)
- `TV_STATE_FILE`: where browser state/cookies are stored
- `TV_EXPORT_ROOT`: folder where raw downloads land
- `TV_SCREEN_URLS`: list of TradingView screener URLs to export
- `TV_HEADLESS`: run browser headless
- `TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE`: delete raw downloads after parsing

### Paths
- `SYMBOLS_DIR`: `data/symbols`
- `OUTPUT_DIR`: `data/outputs`

### SQLite cache
- `DB_PATH`: SQLite path (default: `data/cache/marketdata.sqlite`)
- `SQLITE_WAL_MODE`: recommended `True`
- `SQLITE_CACHE_DAYS_PER_SYMBOL`: how many daily rows to keep per symbol

### Nuking the DB (recommended after schema changes)
If you change `model.sql` or the SQLite schema in `utils/sqlite_store.py`, **delete the DB** so the new schema is created cleanly.

Run:
- `python tools/nuke_db.py` (prompts)
- `python tools/nuke_db.py --yes` (no prompt)

This will:
- move `data/cache/marketdata.sqlite` to a timestamped backup
- remove `-wal` / `-shm`


### Strategy
- `EMA_PERIOD`: default 20
- `CROSS_LOOKBACK_DAYS`: default 30
- `WINDOW_DAYS_PRIMARY`: primary frozen pre-cross window length (e.g., 35)
- `ENABLE_SECONDARY_WINDOW`: enable a second frozen window (default True)
- `WINDOW_DAYS_SECONDARY`: secondary frozen pre-cross window length (e.g., 21)
- `ALLOW_ALERT_ON_CROSS_DATE`: allow alerts when CrossDate == EventDate
- `REARM_ON_REENTRY`: rearm logic enabled
- `REENTRY_MODE`: `strict` or `inclusive`

> Note: `WINDOW_DAYS_SHORT` / `WINDOW_DAYS_LONG` exist only for backward compatibility and are not used by the production logic.

### Cross-universe persistence
- `SAVE_EMA20_CROSS_SYMBOLS`: if True, writes `ema20_cross_YYYY-MM-DD.csv`

### Alerts ledger + safeguards
- `ENABLE_ALERTS_LEDGER`: enables dedupe ledger (`alerts_log`)
- `PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY`: prevents overwriting a non-empty alerts file with an empty run

### Discord
- `DISCORD_ENABLED`: master toggle
- `DISCORD_WEBHOOK_URL`: webhook URL
- `DISCORD_ENV`: label in alerts (`TEST`/`PROD`)
- `DISCORD_SEND_LIVE_ALERTS`: send alerts immediately in live mode
- `DISCORD_SEND_EOD_SUMMARY`: send EOD summary in Step 3
- `DISCORD_SEND_EOD_ALERTS_TABLE`: send EOD alerts table in Step 3
- `DISCORD_MAX_ALERTS`: max alerts printed in table

#### "No confusion" banners (recommended)
These are short, human-readable config summaries that are printed to the console at start/end, and optionally posted to Discord.
- `DISCORD_SEND_STARTUP_BANNER`: post a Live Tracker startup banner (includes window days, interval, session mode)
- `DISCORD_SEND_SHUTDOWN_BANNER`: post a Live Tracker shutdown banner
- `DISCORD_SEND_EOD_BANNERS`: post Step 3 start + done banners

### Live tracker
- `TIMEZONE`: must be `America/Chicago` for your setup
- `LIVE_ENABLED`: master toggle
- `LIVE_POLL_SECONDS`: polling interval
- `LIVE_INTERVAL`: `1m` or `5m`
- `LIVE_UNIVERSE_PREFER_CROSS_FILE`: prefer cross-universe file if present

#### Live session control (pre / regular / post market)
- `LIVE_SESSION_MODE`:
  - `RTH`: regular session only (NYSE open → close)
  - `PRE`: premarket only (`LIVE_PREMARKET_START` → open)
  - `POST`: postmarket only (close → `LIVE_POSTMARKET_END`)
  - `ALL`: pre + regular + post (`LIVE_PREMARKET_START` → `LIVE_POSTMARKET_END`)
- `LIVE_PREMARKET_START`: `HH:MM` Chicago time
- `LIVE_POSTMARKET_END`: `HH:MM` Chicago time
- `LIVE_AUTO_WAIT_FOR_SESSION_START`: if True, wait until run window starts
- `LIVE_AUTO_STOP_AFTER_SESSION_END`: if True, stop when run window ends

### Backtest
- `BACKTEST_MODE`: replay mode (default False)
- `BACKTEST_START_DATE`: YYYY-MM-DD
- `BACKTEST_END_DATE`: YYYY-MM-DD
- `BACKTEST_SAVE_SCAN_ALL`: large debug output

### Performance
- `YF_READ_LIMIT_ROWS`: how many daily rows to read per symbol

---

## Troubleshooting

### “No symbols found to monitor”
You need at least one of:
- `data/symbols/symbols_YYYY-MM-DD.csv` (Step 1)
- `data/symbols/ema20_cross_YYYY-MM-DD.csv` (Step 3)

### Starting at 8:25am CST
Set:
- `LIVE_SESSION_MODE = "RTH"`
- `LIVE_AUTO_WAIT_FOR_SESSION_START = True`

The script will sleep until the NYSE open (8:30am Chicago time).

### Holidays
The live tracker uses `exchange-calendars` with calendar `XNYS`. If the market is closed, it exits.

---

## Optional modules

### Multi-window backtest (EOD)
Use this when you want to compare how different window lengths behave historically **without touching your production state**.

```bash
python tools/multi_window_backtest.py --db data/cache/marketdata.sqlite \
  --start 2025-10-01 --end 2026-01-15 \
  --primary 20,30,35 --secondary 14,21 \
  --rearm_on_reentry 1
```

Outputs:
- `data/backtests/backtest_summary_<timestamp>.csv`
- `data/backtests/backtest_alerts_<timestamp>.csv`

### Daily runner
A convenience wrapper that runs the main scripts in a single command:

```bash
python daily_runner.py --mode morning_prep
python daily_runner.py --mode live
python daily_runner.py --mode eod
```

Notes:
- `morning_prep` assumes Step 3 is configured in `config.py` for TEST_MODE/as-of last trading day.
- `live` runs until you stop it.
- `eod` should be run after the close.

## Daily runner modes

- `morning_prep`: builds the universe and cross-universe file as-of the last trading day (no Discord).
- `live`: runs intraday monitoring and sends live Discord alerts.
- `eod`: finalizes end-of-day outputs and sends Discord summary/table.
- `daily`: runs `morning_prep` → waits for open → `live` → `eod`.

Examples:
```bash
python daily_runner.py --mode morning_prep
python daily_runner.py --mode live
python daily_runner.py --mode eod
python daily_runner.py --mode daily
```
