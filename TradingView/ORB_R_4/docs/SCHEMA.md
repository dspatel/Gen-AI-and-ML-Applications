# SQLite schema notes

## breakout_events (event header)

One row per alert fired.

### Why these extra columns?
We want every alert to be **fully auditable** without recomputation:

- RR variant at trigger time:
  - `include_today_or=0`: RR built from *prior sessions only* (start-of-session)
  - `include_today_or=1`: RR *includes today's OR* (post-OR)

- RR snapshot at trigger time:
  - `ref_low`, `ref_high`, `ref_width`

- Triggering candle snapshot:
  - `bar_open_ts_cst`, `bar_close_ts_cst`
  - `candle_open`, `candle_high`, `candle_low`, `candle_close`
  - `interval`, `orb_minutes`

This is what lets us debug "why did it alert" and build ML features later.

## Updates in 0.7.0
- daily_reference_metrics: added inflation_factor
- breakout_events: added decision/confidence/reasons_json/engine_version
- event_horizon_metrics: added close_pen/wick_pen/body_norm/range_norm