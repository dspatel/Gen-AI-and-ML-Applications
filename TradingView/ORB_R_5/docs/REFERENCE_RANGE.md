# Reference Range (RR) Metrics

This document defines the **Reference Range (RR)** and all supporting metrics stored in SQLite table
`daily_reference_metrics`.

All computations use:
- NYSE trading sessions
- Regular session (RTH) only: 08:30–15:00 America/Chicago
- Intraday candles at `interval` (default 15m)
- Daily Opening Range rows from `opening_ranges` (30-minute OR by default)

## 1) Opening Range (OR) per day
For a session day `d`:

- OR window: `[market_open, market_open + orb_minutes)`
- `or_high = max(high in OR window)`
- `or_low  = min(low  in OR window)`
- `or_width = or_high - or_low`
- `or_mid = (or_high + or_low) / 2`

These are stored in `opening_ranges` (Step 2).

## 2) Reference Range (RR) for horizon H
For **as-of date** `D` and horizon `H`:

1. Select the last `H` **prior trading sessions** before `D`.
2. Use each selected day’s OR band `[or_low, or_high]`.
3. Compute:
   - `ref_high = max(or_high across selected days)`
   - `ref_low  = min(or_low  across selected days)`
   - `ref_width = ref_high - ref_low`

If fewer than `H` complete OR days exist, we record the row as incomplete:
- `sessions_required = H`
- `sessions_available = k`
- `sessions_missing = H-k`
- `is_complete = 0`
- RR outputs are `NULL`

## 3) Supporting metrics for the OR set used to build RR

### 3.1 OR-set composition
Computed across the `k` OR days actually used.

- `or_width_mean`, `or_width_median`, `or_width_min`, `or_width_max`

### 3.2 OR overlap structure (clustered vs shifting ORs)
Let there be `k` OR bands. Total pairs:

- `pairs_total = k*(k-1)/2`

For each pair `(i, j)`:
- `overlap_amt = max(0, min(high_i, high_j) - max(low_i, low_j))`
- Overlaps if `overlap_amt > 0`

Stored metrics:
- `or_overlap_pairs_count`
- `or_overlap_pairs_pct = or_overlap_pairs_count / pairs_total`
- `or_overlap_days_count` = number of days that overlap at least one other day
- `or_overlap_days_pct = or_overlap_days_count / k`
- `overlap_amount_median` = median `overlap_amt` among overlapping pairs (NULL if none)

### 3.3 Stability / drift of OR location
Using per-day `or_mid`:

- `or_mid_median`
- `or_mid_std` (sample std dev across days)
- `or_mid_range = max(or_mid) - min(or_mid)`
- `or_mid_drift_slope` = slope of a simple linear regression of `or_mid` vs day index (0..k-1)

### 3.4 Behavior relative to each day’s own OR
Per day, across all RTH bars:
- `% closes inside own OR`
- `% closes above own OR`
- `% closes below own OR`
- `direction_bias = (% above) - (% below)`
- `range_to_or = session_range / or_width` (session_range from RTH)

Aggregates stored:
- `median_inside_own_or_pct`
- `median_range_to_or`
- `mean_direction_bias`
- `bias_consistency` = fraction of days whose `direction_bias` sign matches the sign of `mean_direction_bias` (zeros ignored)

### 3.5 RR dominance / concentration
How many days set the RR boundaries:
- `ref_high_day_count`: number of days whose `or_high == ref_high` (within tolerance)
- `ref_low_day_count`: number of days whose `or_low == ref_low`
- `ref_extreme_concentration = max(ref_high_day_count, ref_low_day_count) / k`

## 4) Table: daily_reference_metrics
Keyed by:
- `symbol, asof_date_cst, horizon_days, orb_minutes, interval, include_today_or`

All metrics are stored per horizon.
