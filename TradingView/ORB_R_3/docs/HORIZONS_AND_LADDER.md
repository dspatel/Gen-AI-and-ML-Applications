# Horizons + Ladder alerts

## What is a horizon?
A **horizon** is the number of past sessions used to build a reference range.

Example:
- horizons: `[3,5,9]`
- For each symbol, we build **three** reference ranges.

Each horizon produces:
- `ref_low`, `ref_high`, `ref_width`
- behavior aggregates (median % inside each day’s own OR, etc.)
- OR overlap stats (how clustered/disjoint the past ORs are)

## Ladder breakout idea
We run breakout detection against **all active horizons**.

As the day progresses:
- the first time price confirms a break of a given horizon → event recorded
- later breaks of other horizons → additional events recorded
- if multiple horizons break on the same bar, we record all and mark them as `simultaneous_horizons`

We also track:
- `broken_horizons_before` (what was already broken for that direction)
- `broken_horizons_after` (what’s broken after this event)

## Why this helps
- Small horizon breaks can be “early warnings”
- Larger horizon breaks can represent stronger regime shift
- Overlap + inflation help you see if the past ORs are clustered (easy to trust a break) vs disjoint (more volatility)

