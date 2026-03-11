
This repository builds an ORB (Opening Range Breakout) analytics pipeline with a reference-range
layer derived from prior days' opening ranges.

Through Step 5, the system can:
- Identify valid market sessions and OR windows (handles weekends/holidays)
- Fetch intraday bars for any backdate (asof_date) deterministically with caching
- Compute per-day Opening Range (OR) metrics
- Build a reference range for today: max(ORH) and min(ORL) over lookback days
- Compute past behavior metrics relative to each day's own OR (not today's reference range)
- Emit a day-centric report across all symbols


## Metrics documentation
See `docs/METRICS_GUIDE.md` for formulas, definitions, and intuition mapping.
