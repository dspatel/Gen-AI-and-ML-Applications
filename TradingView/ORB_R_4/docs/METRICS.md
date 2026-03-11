# Metrics

## Opening Range (OR)
OR window: `[market_open, market_open + orb_minutes)`
- `or_high = max(high in OR window)`
- `or_low  = min(low in OR window)`
- `or_width = or_high - or_low`

Stored in: `opening_ranges`.

## Reference Range (RR) per horizon
For as-of `D` and horizon `H` (prior sessions):
- `ref_high = max(or_high across selected days)`
- `ref_low  = min(or_low across selected days)`
- `ref_width = ref_high - ref_low`

RR phase:
- `include_today_or=0` pre-OR RR (prior sessions only)
- `include_today_or=1` post-OR RR (includes today's OR after OR completes)

Stored in: `daily_reference_metrics`.

## OR overlap (stability)
- `or_overlap_pairs_pct`: fraction of overlapping OR pairs
- `or_overlap_days_pct`: fraction of days overlapping at least one other

High overlap → clustered ORs; low overlap → shifting ORs.

## Behavior aggregates (relative to each day’s own OR)
- `median_inside_own_or_pct`
- `median_range_to_or`
- `mean_direction_bias`
- `bias_consistency`

## Inflation factor (RR stretch)
- `inflation_factor = ref_width / median(or_width)` over the days used for RR

Intuition:
- 🧊 tight RR, ⚖️ balanced, 🔥 stretched
