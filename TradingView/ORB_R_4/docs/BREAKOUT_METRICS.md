# Breakout metrics

## Close-only breakout
- UP: `close > ref_high`
- DOWN: `close < ref_low`

## Intensity
- `breakout_amt` and `breakout_strength = breakout_amt / ref_width`

## Trigger candle quality (relative to RR snapshot)
- `close_pen`: close penetration beyond boundary / ref_width
- `wick_pen`: opposing wick beyond boundary / ref_width
- `body_norm`: candle body / candle range
- `range_norm`: candle range / ref_width

Stored in: `event_horizon_metrics`.
