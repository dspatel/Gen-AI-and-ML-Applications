# EMA20 Anchored Breakout Scanner

End-of-day (EOD) scanner + live intraday tracker (yfinance) built around an **EMA20 “touch/cross” regime reset** and **anchored pre-cross windows**.

- **Daily candles** (Yahoo Finance) power the crossover detection and frozen windows.
- **Live intraday prices** (Yahoo Finance 1m/5m) are used to trigger **real-time Discord alerts**.
- A **SQLite alerts ledger** dedupes alerts across LIVE + EOD runs and prevents accidental overwrite issues.

---

## Strategy: rules and mental model

### EMA20 cross definition
A daily bar is considered a **cross/touch** if:

> `Low ≤ EMA20 ≤ High`

This matches your requirement: “cross even if close is not on the other side.”

Direction is recorded as:
- **UP** if `Close ≥ EMA20`
- **DOWN** otherwise

### Frozen windows (anchored to CrossDate)
When the **latest CrossDate** is identified (within last `CROSS_LOOKBACK_DAYS`, default 30 trading days), we freeze:

- **7D pre-cross window**
  - `WindowHigh_7D_preCross = max(High)` over the **7 trading days BEFORE CrossDate**
  - `WindowLow_7D_preCross = min(Low)` over the **7 trading days BEFORE CrossDate**

- **21D pre-cross window**
  - `WindowHigh_21D_preCross = max(High)` over the **21 trading days BEFORE CrossDate**
  - `WindowLow_21D_preCross = min(Low)` over the **21 trading days BEFORE CrossDate**

CrossDate itself is excluded.

### Event conditions (evaluated daily / live)
**LONG**
- `Price > WindowHigh_7D_preCross` AND `Price > EMA20`

**SHORT**
- `Price < WindowLow_7D_preCross` AND `Price < EMA20`

Where **Price** is:
- EOD scan: **today’s close**
- Live tracker: **latest intraday price** (and EMA20 is computed using a tentative “today” daily bar)

### Arming / Disarming / Rearming
- When a **new CrossDate** is detected → `armed = 1`
- When an alert triggers → `armed = 0`
- If `REARM_ON_REENTRY = True`, we re-arm when price re-enters the **7D window**:
  - strict: `WindowLow < Price < WindowHigh`
  - inclusive: `WindowLow ≤ Price ≤ WindowHigh`

---

## Flow diagram

```mermaid
flowchart TD
  A[Load symbols universe] --> B[Read daily bars from SQLite]
  B --> C[Find latest EMA20 touch within lookback]
  C -->|none| Z[Skip symbol]
  C --> D[If new cross: freeze 7D and 21D windows]
  D --> E[Persist state: cross + windows + armed=1]
  E --> F[Evaluate LONG/SHORT using price & EMA]
  F -->|Signal| G[Insert into alerts_log ledger (dedupe)]
  G --> H[Disarm symbol]
  F -->|No signal| I[Optional rearm on re-entry]
```

---

## Project structure

- `run_step2_fetch_yf_to_sqlite.py`  
  Fetches daily candles from Yahoo and stores them in SQLite (`daily_bars`), including `ema20`, `ema20_h`, `ema20_l`.

- `run_step3_scan_from_sqlite.py`  
  EOD scan: filters cross-eligible symbols, computes/loads frozen windows, generates scan outputs, and posts optional Discord summary/table.
  Also writes the **cross universe** file if enabled:
  - `data/symbols/ema20_cross_YYYY-MM-DD.csv`

- `run_live_tracker_yf.py`  
  Intraday tracker: waits for NYSE open in **America/Chicago**, polls yfinance (1m/5m), triggers alerts live, writes to ledger, and posts Discord alerts.

- `utils/sqlite_store.py`  
  SQLite schema + migrations + alert ledger functions.

- `utils/discord_notify.py`  
  Discord webhook sender + formatting functions.

---

## Database tables

### `daily_bars`
Stores daily OHLCV plus EMA metrics:
- `ema20`, `ema20_h`, `ema20_l`

### `symbol_state`
Per-symbol state across sessions:
- `last_cross_date`, `last_cross_direction`
- `window_high_7`, `window_low_7`
- `window_high_21`, `window_low_21`
- `armed`
- `last_alert_date`, `last_alert_signal`

### `alerts_log` (ledger)
Append-only ledger with a **unique constraint**:
- `UNIQUE(symbol, event_date, signal, cross_date)`

This dedupes:
- multiple live alerts
- live vs EOD duplicates
- multiple EOD runs

---

## Key safeguards

### Prevent overwriting alerts with 0 alerts
`PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY = True` prevents accidental reruns from wiping an existing non-empty alerts file.

### Live tracker does NOT write alerts CSVs
Live tracker only:
- writes to ledger
- sends Discord alerts
EOD scan owns the daily CSV outputs.

---

## Configuration (config.py)

Important toggles:

- Save cross-universe file:
  - `SAVE_EMA20_CROSS_SYMBOLS = True`

- 21D window:
  - `WINDOW_DAYS_LONG = 21`

- Ledger + safeguard:
  - `ENABLE_ALERTS_LEDGER = True`
  - `PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY = True`

- Discord:
  - `DISCORD_ENABLED = True/False`
  - `DISCORD_WEBHOOK_URL = "..."`

- Live:
  - `LIVE_ENABLED = True/False`
  - `LIVE_INTERVAL = "1m" or "5m"`
  - `LIVE_POLL_SECONDS = 60`
  - `TIMEZONE = "America/Chicago"`
  - Session control:
    - `LIVE_SESSION_MODE = "RTH" | "PRE" | "POST" | "ALL"`
    - `LIVE_PREMARKET_START = "07:00"`
    - `LIVE_POSTMARKET_END = "17:00"`

---

## How to run

### Install dependencies
```bash
pip install -r requirements.txt

# Required once for Step 1 (Playwright browser)
python -m playwright install chromium
```

### Step 2: update daily bars
```bash
python run_step2_fetch_yf_to_sqlite.py
```

### Step 3: EOD scan
```bash
python run_step3_scan_from_sqlite.py
```

### Live tracker (intraday)
```bash
python run_live_tracker_yf.py
```

The live tracker will:
- detect NYSE open/close using `exchange-calendars`
- operate in **America/Chicago**
- wait until open if started early
- stop after close if enabled

---
