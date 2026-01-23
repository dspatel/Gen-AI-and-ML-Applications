# Changelog

## v4 (2026-01-02)
- Added one-time Discord notification when the Opening Range is established (per symbol, persisted across restarts)
- Persisted OR-notification flag in SQLite state to prevent duplicate OR-created messages after restart

## v3 (2026-01-02)
- Added SQLite persistent state (`state/orb_state.sqlite`) to prevent duplicate alerts after restarts
- LIVE mode anchors session date to today and waits pre-market (no early exit before open)
- Fixed re-arm handling so LIVE won't re-alert after catchup unless price closes back inside OR


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
