# VCRE Monitor (tvDatafeed) — Project Files

## Files
- `monitor_vcre.py` — main scanner (15m candles by default), sends email alerts, writes Excel log
- `symbols.json` — list of symbols + preferred exchange (with automatic fallback)
- `setup_keyring.py` — one-time helper to store TradingView credentials securely in OS keychain
- `requirements.txt` — Python dependencies

## Quick start
1) Install deps:
   - `pip install -r requirements.txt`

2) Add symbols (or enable/disable) in `symbols.json`.

3) Store TradingView creds (one time):
   - `python setup_keyring.py`

4) Configure email:
   - Open `monitor_vcre.py`
   - Fill `EMAIL_CFG` with your SMTP info and app password.

5) Run monitor:
   - `python monitor_vcre.py`

## Candle duration
Change:
```python
CANDLE_INTERVAL = Interval.in_15_minute
```
Examples:
- `Interval.in_1_minute`
- `Interval.in_5_minute`
- `Interval.in_1_hour`
- `Interval.in_daily`

## Alert timing (after candle closes)
The script only evaluates when the **latest bar timestamp changes** (bar-close guard), so even if you poll every 30 seconds, you will not alert multiple times on the same candle.

## Stop-loss preference (edit in monitor_vcre.py)
```python
STOP_MODE = "ANCHOR_ATR_BUFFER"
ATR_STOP_MULT = 1.5
ATR_BUFFER_MULT = 0.25
```

STOP_MODE options:
- `ANCHOR`
- `ATR`
- `ANCHOR_ATR_BUFFER` (recommended default)
- `WIDER_OF_ANCHOR_ATR`
- `TIGHTER_OF_ANCHOR_ATR`

## Excel log
Alerts are appended to `vcre_alert_log.xlsx` in the script folder.

## Debugging
Set:
```python
DEBUG = True
```
Key debug outputs appear per symbol:
- latest two candles + computed indicators
- breakout counts and volume flags
- alert summary on send
