# Changelog

## v2
- Added run.bat (Windows quick start)
- Added symbols.txt (edit symbols without changing python code)
- Added orb_monitor/config_local.py template for secrets
- Config auto-loads config_local.py if present
- Added PROJECT_CONTEXT.md (shareable project summary)

## v1
- Yahoo/yfinance data source (native intervals only; no 10m resampling)
- Opening Range Breakout using first 30 minutes from session open
- True breakout requires 2-candle confirmation
- Re-arm: must re-enter range before subsequent breakout alerts
- Catchup scan at startup (logs + notifies historical breakouts for the day)
- Discord alerts + market close daily summary
- LIVE/TEST log separation
