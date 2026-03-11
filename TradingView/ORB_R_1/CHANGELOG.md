## v0.2.0
- Added multi-horizon breakout engine and replay_day tool with Discord notifications.
- Added breakout_events and event_horizon_metrics tables.
- Added requests dependency for Discord webhook.

# Changelog

## v0.1.0
- Initial clean skeleton + DB-first OR + coverage-based RR validity.

## v0.1.1
- Fix: robust OHLC normalization for yfinance MultiIndex/tuple columns (prevents AttributeError).
- Improvement: OR audit now includes exception message (not just exception type) for faster debugging.

## v0.1.2
- Fix: correctly extract OHLC when yfinance returns MultiIndex columns where the ticker and field levels are reversed or nested.
- Fix: prevent collapsing MultiIndex to repeated ticker labels (e.g., ['spy','spy',...]) by explicitly selecting the symbol level and the OHLC field level.

## v0.1.3
- Added OR overlap metrics to RR output: pair overlap counts/% and day overlap counts/%.
- Added behavior metrics (relative to each day’s own OR): median inside %, median range-to-OR, mean direction bias, bias consistency.
- Added behavior-availability audit fields; missing intraday days do **not** invalidate RR.
- Added a lightweight SQLite migration to add new RR columns when upgrading an existing DB.
