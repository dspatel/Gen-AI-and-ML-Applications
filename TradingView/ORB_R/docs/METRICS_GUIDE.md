
# Metrics & Intuition Guide

This document explains every metric produced by the project and how the "intuition labels" are derived.

## Data Concepts

### Session
A trading day for the configured exchange calendar (default: `XNYS`) in the configured timezone (default: `America/Chicago`).

### Bar
An intraday OHLCV candle (e.g., `1m` or `15m`), indexed by timestamp.

### Opening Range (OR)
For a given session date, the OR window is the first `orb_minutes` after market open (e.g., 30 minutes).
- `or_start` = session open timestamp
- `or_end`   = `or_start + orb_minutes`

Within `[or_start, or_end)`:

- `ORH` (or_high) = max(High)
- `ORL` (or_low)  = min(Low)
- `ORW` (or_width)= ORH - ORL

## Reference Range (Lookback-anchored range)

We form a *reference range for today's analysis* by looking back across prior sessions’ ORs.

Let the selected lookback session set be **S**.
- If `include_today_or = false`, then **S** contains only prior sessions.
- If `include_today_or = true`, then **S** also includes today's OR window.

For each day `d ∈ S`, we compute `(ORH_d, ORL_d)`.

Then:

- `ref_high` = max_d(ORH_d)
- `ref_low`  = min_d(ORL_d)
- `ref_width` = ref_high - ref_low

**Intuition:** this creates a "superset range" representing the widest OR boundary observed across the chosen lookback days.

## Daily Metrics Output (`<date>_metrics.csv`)

Each row is one `(asof_date, symbol)`.

### Session counters
- `sessions_requested`  
  Number of session dates the system *attempted* to include (based on exchange calendar and lookback settings).

- `sessions_nonempty`  
  Number of requested sessions that returned non-empty intraday bars from the data provider.

- `sessions_used`  
  Number of sessions for which we successfully computed an OR (i.e., the OR window slice had bars and produced ORH/ORL).

- `sessions_missing_data`  
  Comma-separated list of session dates where provider data was empty/missing.

**Intuition:** if `sessions_used < sessions_requested`, either the provider returned no intraday bars or the OR window was empty for those days.

### Reference range metrics
- `ref_high`, `ref_low`, `ref_width`  
  Defined above.

### OR overlap metrics
These quantify how similar the lookback ORs are to each other.

- `or_overlap_ratio`  
  Measures the average overlap of daily OR intervals within the reference range.

  For each day `d`, define:
  - overlap_d = max(0, min(ORH_d, ref_high) - max(ORL_d, ref_low))  
    (since OR sits inside ref range by construction, overlap_d is essentially ORW_d)
  Then:
  - overlap_ratio_d = overlap_d / ref_width  
  And:
  - `or_overlap_ratio` = median(overlap_ratio_d over d∈S)  

**Intuition:** higher overlap ratio means daily ORs are consistently wide relative to the ref range; lower means ref range is widened by outlier days.

- `inflation_factor`  
  `inflation_factor = ref_width / median(ORW_d)` across used sessions.

**Intuition:** how "inflated" the combined ref range is compared to a typical day’s OR.

### Lookback behavior metrics
These capture how price behaved relative to each day’s OR.

For each lookback day `d`:
- `% inside own OR`: percentage of bars that closed within `[ORL_d, ORH_d]`
- `range_to_or`: (session high - session low) / ORW_d
- `direction_bias`: signed measure of whether closes tended to be above OR midpoint vs below

We aggregate across lookback days:

- `median_inside_own_or_pct`  
  median of daily “% closes inside own OR”.

**Intuition:** consolidation vs expansion tendency in lookback.

- `median_range_to_or`  
  median of daily `range_to_or`.

**Intuition:** how large the full day’s range tends to be vs its own OR.

- `mean_direction_bias`  
  average directional tendency across lookback days.

**Intuition:** positive = upward skew; negative = downward skew.

- `bias_consistency`  
  fraction of lookback days sharing the same sign as `mean_direction_bias`.

**Intuition:** near 1 means the directional skew is consistent; near 0.5 means mixed.

## Events Output (`<date>_events.csv`)

Each row is one breakout event for one symbol, on the as-of session.

### Breakout detection (close-only)
We trigger an **UP breakout** when:
- `Close > ref_high`

We trigger a **DOWN breakout** when:
- `Close < ref_low`

### Re-arm logic
After an UP breakout, we disarm until:
- `Close <= ref_high - reset`

After a DOWN breakout, we disarm until:
- `Close >= ref_low + reset`

Where:
- `reset = inside_reset_pct * ref_width`

**Intuition:** prevents repeated alerts while price chops outside the boundary.

### Intensity metrics (normalized by `ref_width`)
Computed on the breakout bar:

- `close_pen`  
  UP: max(0, Close - ref_high) / ref_width  
  DOWN: max(0, ref_low - Close) / ref_width

**Intuition:** how strongly the breakout closed beyond the boundary.

- `wick_pen`  
  UP: max(0, High - ref_high) / ref_width  
  DOWN: max(0, ref_low - Low) / ref_width

**Intuition:** how far price *reached* beyond the boundary (including wick).

- `body_norm`  
  abs(Close - Open) / ref_width

**Intuition:** breakout candle body size relative to the range.

- `range_norm`  
  (High - Low) / ref_width

**Intuition:** breakout candle total range relative to the reference range.

## Intuition Labels (LabelEngine)

Labels are produced from a YAML configuration (`config/labels.yml`) so the language and thresholds are easy to modify.

Typical label families:
- **open_alignment**: whether today's OR sits centered vs skewed within the reference range
- **reference_shape**: tight vs stretched reference range (often from inflation_factor)
- **regime**: consolidation vs expansion (often from median_inside_own_or_pct and median_range_to_or)
- **direction_bias**: up-lean vs down-lean (from mean_direction_bias + bias_consistency)
- **breakout_strength**: weak vs strong (from close_pen/body_norm/wick_pen)

### How combined intuition is formed
We deliberately avoid a single opaque “score”.
Instead we emit a compact set of orthogonal labels:
- **Context (range + regime)**
- **Directional skew**
- **Breakout quality**

The notification templates combine these into a short message (see `config/notification_templates.yml`).

## Where to modify the language
- `config/labels.yml` controls thresholds and label text
- `config/notification_templates.yml` controls the final message formatting


## Decision & Confidence (Step 8)

A rule-based decision layer converts the context + breakout quality into a simple action and confidence.

### Outputs (events)
- `decision`: `LONG` for UP breakouts, `SHORT` for DOWN breakouts
- `confidence`: 0–1 weighted score from reference/regime/bias/breakout_quality
- `decision_reasons`: short list explaining what drove the confidence

### Where to tune it
- `config/decision_rules.yml` controls thresholds, weights, and human-readable reasons.

### Why this is not a single opaque score
We keep both:
1) **Raw metrics** (for analysis/ML)
2) **A transparent decision** (for fast human use)

So you can adjust rules without changing the underlying dataset.
