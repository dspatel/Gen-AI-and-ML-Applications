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

- **7D window**
  - `WindowHigh_7D_preCross = max(High)` over the 7 trading days BEFORE CrossDate
  - `WindowLow_7D_preCross  = min(Low)`  over the 7 trading days BEFORE CrossDate

- **21D window**
  - `WindowHigh_21D_preCross = max(High)` over the 21 trading days BEFORE CrossDate
  - `WindowLow_21D_preCross  = min(Low)`  over the 21 trading days BEFORE CrossDate

These window values are stored in SQLite (`symbol_state`) and are not recomputed until a **new CrossDate** is detected.

### 3) Alert conditions
Alerts are evaluated using the frozen **7D** window and EMA20 filter:

**LONG**
- `Price > WindowHigh_7D_preCross` AND `Price > EMA20`

**SHORT**
- `Price < WindowLow_7D_preCross` AND `Price < EMA20`

Where **Price** is:
- EOD scanner: *today’s close*
- Live tracker: *latest intraday price* (and daily EMA20 is computed by adding a synthetic “today” row from intraday high/low/close)

The 21D window is included as additional context in outputs and Discord messages.

### 4) Arming / disarming / re-arming
State machine per symbol:

- New CrossDate → `armed = 1`
- When an alert fires → `armed = 0`
- If `REARM_ON_REENTRY = True`, re-arm when Price re-enters the **7D** window:
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

### Strategy
- `EMA_PERIOD`: default 20
- `CROSS_LOOKBACK_DAYS`: default 30
- `WINDOW_DAYS`: short pre-cross window (default 7)
- `WINDOW_DAYS_LONG`: long pre-cross window (default 21)
- `ALLOW_ALERT_ON_CROSS_DATE`: allow alerts when CrossDate == EventDate
- `REARM_ON_REENTRY`: rearm logic enabled
- `REENTRY_MODE`: `strict` or `inclusive`

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
