# ORB Monitor (Yahoo) — Live True Breakout Alerts (Discord/Email)

This project monitors one or more symbols and detects **TRUE Opening Range Breakouts (ORB)**:

- **Opening Range (OR)** = High/Low of the first 30 minutes after market open
- **True breakout** = breakout candle closes outside OR, and the **next candle** closes further in the same direction
- **Re-arm rule** = after a true breakout, the script waits until price closes back inside the OR before detecting another

It sends alerts **as soon as the breakout is confirmed** (at the close of the confirmation candle), logs events to CSV, supports mid-day starts with catchup notifications, and posts a market close summary.

---

## Features

✅ Multi-symbol monitoring (SPY, TSLA, NVDA by default)  
✅ Configurable candle size **(Yahoo/yfinance native intervals only: 1m/2m/5m/15m/30m/60m/90m)**  
✅ Opening Range always built from **market open**, even if script starts mid-day (catchup mode)  
✅ Catchup scan: logs + notifies all confirmed breakouts between market open and script start time  
✅ Live mode: waits for candle close + grace and alerts immediately when confirmed  
✅ Re-arm logic: after first true breakout, no additional signals until price closes back inside OR  
✅ Discord alerts (recommended), Email optional  
✅ Market close notification + daily summary per symbol  
✅ Separate logs for LIVE vs TEST mode (no mixing)

---

## Project structure

```
.
├─ run_live.py
├─ requirements.txt
├─ README.md
└─ orb_monitor/
   ├─ __init__.py
   ├─ config.py
   ├─ data.py
   ├─ strategy.py
   ├─ notify.py
   └─ loggers.py
```

---

## Setup

### Create virtual environment (recommended)
Windows PowerShell:
```bash
python -m venv venv
.\venv\Scripts\activate
```

Mac/Linux:
```bash
python -m venv venv
source venv/bin/activate
```

### Install dependencies
```bash
pip install -r requirements.txt
```

---

## Configuration

Edit `orb_monitor/config.py`:

- `symbols`: list of tickers to monitor
- `candle_minutes`: one of `1,2,5,15,30,60,90`
- `tz`: keep `America/Chicago` for CST/CDT
- `discord_webhook_url`: your webhook

---

## How ORB is computed

- Market open = `08:30` in `America/Chicago` (configurable)
- Opening Range window = first 30 minutes from open
- If `candle_minutes = 5`, OR window = first 6 candles
- OR High = max(high) of those candles
- OR Low = min(low) of those candles

---

## True breakout definition

Breakout requires two candles:

1) Breakout candle closes outside the OR:
- Up: `breakout_close > ORH`
- Down: `breakout_close < ORL`

2) Confirmation candle closes further in the same direction:
- Up: `confirm_close > breakout_close`
- Down: `confirm_close < breakout_close`

Alert fires after the confirmation candle closes.

---

## Re-arm rule (anti-spam)

After a confirmed breakout:
- script becomes **disarmed**
- it will not alert again until a candle close returns **inside** the OR
- then it becomes **armed** again

---

## Catchup behavior (mid-day start)

When started mid-day:
1. Fetch session data
2. Build OR from first 30 minutes since market open
3. Scan forward from after OR window up to “now”
4. Log + send alerts for each confirmed breakout in that period
5. Enter live mode and only alert on new confirmed breakouts going forward

---

## Logging

Logs go to:

- LIVE: `output/LIVE/breakouts_YYYY-MM-DD_{candle}m.csv`
- TEST: `output/TEST/breakouts_YYYY-MM-DD_{candle}m.csv`

---

## Test mode (market closed / after-hours)

In `orb_monitor/config.py`:
```python
test_mode = True
test_date = "2025-12-31"
```

This writes to `output/TEST/...` so logs don’t mix with LIVE.

---

## Running

From project root:
```bash
python run_live.py
```


## Catchup notifications

By default, the monitor **logs** catchup events (events that happened earlier today before you started the script), but does **not** notify them to avoid spam.

Enable catchup notifications:

- Set `send_catchup_notifications = True` in `orb_monitor/config_local.py` (or `orb_monitor/config.py`).
- Optionally cap messages with `catchup_notify_limit_per_symbol`.


## Architecture
See `docs/ARCHITECTURE.md`.


## Versioning
Current version tracked in `VERSION`.
