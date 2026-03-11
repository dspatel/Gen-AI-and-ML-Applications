# Schema

Primary DB:

- `artifacts/r6_stable/orb_core.sqlite`

Core tables:

1. `candles`
- Intraday OHLCV bars (15m default), UTC + CST timestamps, source tag.

2. `opening_ranges`
- Per session OR metrics (`or_high`, `or_low`, `or_range`, completeness flags).

3. `daily_reference_metrics`
- Per session and horizon (`3/5/9`) RR metrics:
- `ref_high`, `ref_low`, `ref_width`
- `inflation_factor`
- overlap and bias metrics (`or_overlap_pairs_pct`, `mean_direction_bias`, `bias_consistency`)
- completion and coverage fields.

4. `breakout_events`
- Event candle, decision, confidence, phase (`include_today_or`), primary horizon.

5. `event_horizon_metrics`
- Horizon-level breakout context:
- `breakout_strength`, `close_pen`, `wick_pen`, `body_norm`, `range_norm`, `clean_break`.

6. `r6_trades`
- Simulated trade records by variant:
- entry/stop/exit prices and times
- `r_mult`, `ret_pct`
- `primary_horizon`, `include_today_or`
- context (`inflation_factor`, `overlap_pairs_pct`, `ref_width`, `break_confluence`)
- `exit_reason`, `exit_variant`.

7. `r6_metrics`, `r6_yearly_returns`, `r6_subset_performance`, `r6_exit_reason_performance`
- Aggregate analytics outputs per run and variant.

8. `r6_strategy_runs`
- Run metadata and status (`completed`/`failed`).
