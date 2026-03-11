# METRICS_GUIDE.md

This project stores two types of daily analytics:

1) **Opening Range (OR)** per session day (table: `daily_or`)
2) **Reference Range (RR) + derived metrics** per `(asof_date, symbol, horizon_days)` (table: `daily_reference_metrics`)

All percentages are expressed as fractions in `[0, 1]`.

## Opening Range (OR)

For a session date `d`, with OR window `[open, open + orb_minutes)`:

- `or_high = max(high in OR window)`
- `or_low  = min(low in OR window)`
- `or_width = or_high - or_low`

## Reference Range (RR)

For as-of date `D` and horizon `H`:

- Select the last `H` prior trading sessions (plus optional `D` if `include_today_or=true` and today's OR is ready).
- For each selected day, use its stored OR band `[or_low, or_high]`.

Then:

- `ref_high = max(or_high across selected days)`
- `ref_low  = min(or_low across selected days)`
- `ref_width = ref_high - ref_low`

## Coverage-based validity (vNext)

RR rows are **evaluated**, not assumed valid:

- `required_days = count(required session dates)`
- `available_days = count(daily_or rows available for those dates)`
- `coverage_ratio = available_days / required_days`
- `is_valid = 1 if coverage_ratio >= min_coverage_ratio else 0`

Invalid RR means: **skip breakout checks for that horizon**, but keep the tracker running.

## OR overlap metrics (computed from available OR days)

Given `N` OR bands for the horizon:

- `pairs_total = N*(N-1)/2`

For each pair `(i, j)`:

- `overlap_amt = max(0, min(high_i, high_j) - max(low_i, low_j))`
- The pair overlaps if `overlap_amt > 0`

Aggregates:

- `or_overlap_pairs_count = number of overlapping pairs`
- `or_overlap_pairs_pct = or_overlap_pairs_count / pairs_total`
- `or_overlap_days_count = number of days that overlap at least one other day`
- `or_overlap_days_pct = or_overlap_days_count / N`

## Behavior metrics (relative to each day’s OWN OR)

For each available day `d`:

- `inside_own_or_pct = % of session closes in [or_low_d, or_high_d]`
- `above_own_or_pct = % of session closes > or_high_d`
- `below_own_or_pct = % of session closes < or_low_d`
- `direction_bias_d = above_own_or_pct - below_own_or_pct`
- `range_to_or_d = (session_high_d - session_low_d) / or_width_d`

Aggregated across days in the horizon:

- `median_inside_own_or_pct = median(inside_own_or_pct)`
- `median_range_to_or = median(range_to_or_d)`
- `mean_direction_bias = mean(direction_bias_d)`
- `bias_consistency = fraction of non-zero daily biases matching the sign of mean_direction_bias`

### Behavior availability audit

Behavior metrics require intraday bars for each day. Missing intraday **does not invalidate** RR.

- `behavior_days_required = number of OR days considered for behavior (usually available OR days)`
- `behavior_days_available = number of days with intraday available`
- `behavior_days_missing_json = JSON list of missing session dates`
- `behavior_failure_reason = '' | 'partial_behavior_days_missing' | 'no_behavior_days_available'`
