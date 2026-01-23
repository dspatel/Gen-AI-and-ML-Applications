# ORB Monitor Project Context (Share this file with ChatGPT next time)

Last updated: 2026-01-20 00:00:00

## Goal
Monitor multiple stock symbols (default: SPY, TSLA, NVDA) using Yahoo/yfinance intraday candles and detect
**TRUE Opening Range Breakouts (ORB)**. Send **immediate notifications** on confirmation and log all events.

## Data source
- **Yahoo Finance via yfinance**
- Intraday candle intervals restricted to native Yahoo/yfinance intervals:
  **1m, 2m, 5m, 15m, 30m, 60m, 90m**
- No TradingView login or tvdatafeed is used in this version.

## Timezone & session
- All timestamps are converted to `Config.tz` (default: **America/Chicago**).
- Session time defaults:
  - Open: **08:30** Chicago
  - Close: **15:00** Chicago

## Opening Range (OR) definition
- OR is computed from the **first 30 minutes after session open**.
- `orb_bars = orb_minutes / candle_minutes`
  - Example: 30m OR with 5m candles => first 6 candles define OR.

## True breakout definition (2-candle confirmation)
A breakout is only "TRUE" if:
1) **Breakout candle** closes outside OR:
   - Up: breakout_close > ORH
   - Down: breakout_close < ORL
2) **Confirmation candle** (next candle) closes further in the same direction:
   - Up: confirm_close > breakout_close
   - Down: confirm_close < breakout_close

Alerts are sent **immediately after the confirmation candle closes** (not end of day).

## Re-arm rule (avoid repeated alerts)
After a TRUE breakout is confirmed:
- The symbol becomes **disarmed**
- No more alerts until a candle closes **back inside the OR range**
- Then the symbol becomes **armed** again

## Catchup behavior (start script mid-day)
When started during market hours:
- Pulls session bars, builds OR from the first 30 minutes since open
- Scans from after OR window up to the startup time
- Logs + sends notifications for any TRUE breakouts in that period (marked `is_catchup=True`)
- Then enters live monitoring for new events

## Persistent state (restart-safe)
To prevent duplicate notifications after script restarts, the monitor persists per-symbol session state
to a local SQLite database:

- Path: `state/orb_state.sqlite`
- Stored fields: `or_ready`, `or_high`, `or_low`, `or_notified`, `armed`, `last_confirm_dt_processed`

On startup, if state exists for the current session date, it is restored before catchup/live processing.

## Pre-market start behavior
In LIVE mode, if you start the script before the session opens, Yahoo Finance often only has yesterday's bars.
To avoid exiting early, LIVE mode anchors the session date to **today** in `Config.tz` and **waits until open**
before starting live monitoring.

## Notifications
- Discord webhook supported (recommended).
- Optional: one-time notification when the Opening Range is established for each symbol (toggle: `Config.notify_on_or_creation`).
- Email via Outlook SMTP is present but disabled by default.
- Webhook and secrets are stored in **orb_monitor/config_local.py** (not committed).

## Logging
CSV is written to:
- LIVE: `output/LIVE/breakouts_YYYY-MM-DD_{candle}m.csv`
- TEST: `output/TEST/breakouts_YYYY-MM-DD_{candle}m.csv`

Optional candle-level debug trace (recommended when diagnosing timezone / missed or shifted alerts):
- LIVE: `output/LIVE/candles_{SYMBOL}_YYYY-MM-DD_{candle}m.csv`
- TEST: `output/TEST/candles_{SYMBOL}_YYYY-MM-DD_{candle}m.csv`

Each candle-debug row captures the two closed bars evaluated (breakout `b` + confirm `c`), the OR levels,
armed state, and a reason code (e.g., `NO_BREAKOUT`, `DEDUPED`, `REARM_WAIT`, `BREAKOUT`).

Each event logs:
- ORH / ORL
- direction (UP_TRUE / DOWN_TRUE)
- breakout candle time/close/volume
- confirmation candle time/close/volume
- is_catchup flag

## Testing / after-hours
- Set `test_mode=True` and `test_date="YYYY-MM-DD"` in `orb_monitor/config.py`.
- Logs go to `output/TEST/` so they do not mix with live logs.

## Files & responsibilities
- `run_live.py`: main runner (catchup + live loop + market close summary)
- `orb_monitor/config.py`: configuration (loads optional `config_local.py`)
- `orb_monitor/data.py`: fetches OHLCV from Yahoo/yfinance and localizes timestamps
- `orb_monitor/strategy.py`: ORB rules, catchup scan, live detection, re-arm logic
- `orb_monitor/notify.py`: Discord message formatting + posting, optional email function
- `orb_monitor/loggers.py`: CSV logging with LIVE/TEST separation

## Convenience files
- `run.bat`: Windows one-click runner (creates venv + installs requirements + runs)
- `symbols.txt`: one symbol per line; overrides `Config.symbols` if present
- `orb_monitor/config_local.py`: local secrets (Discord webhook / email password)

## Current status
- Script runs in live mode during market hours and stops at close.
- Sends market close summary to Discord.
- Catchup events are not shown verbosely in the terminal dashboard but are logged and notified.

## Next likely enhancements (future)
- Add email notifications toggle + better templates
- Add richer per-event metrics (ATR, VWAP, EMA, volume spike, etc.)
- Add persistent state so restarts don't re-notify the same catchup events
- Add robust market calendar/holiday handling
