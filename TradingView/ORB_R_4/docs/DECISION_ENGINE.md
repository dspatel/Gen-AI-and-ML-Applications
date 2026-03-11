# Decision engine

We store decision output on `breakout_events`:
- decision (LONG/SHORT/NO_TRADE)
- confidence (0–1)
- reasons_json (short tags)
- engine_version

Current engine: v1.0.0
Signals (primary horizon):
- breakout_strength, close_pen, wick_pen
- inflation_factor, OR overlap pct
- mean_direction_bias + bias_consistency
- also_count (multi-horizon)
- include_today_or (pre/post OR context)
