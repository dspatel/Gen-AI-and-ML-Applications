# Live Simulation

## Purpose

Validate end-to-end behavior without placing broker orders.

## Command

- `python -m agent.main --mode paper --dry-run`

## What dry-run does

1. Runs reselection if due
2. Loads strategy map
3. Pulls signal bars
4. Evaluates entries/exits
5. Writes summaries/events
6. Does not submit/cancel Alpaca orders

## Outputs

- `live_trade_summary.json`
- `live_events` rows
- `live_positions` rows only if your dry-run path is configured to persist opened state in future updates

## Transition to paper execution

1. Confirm dry-run behavior for several sessions
2. Remove `--dry-run`
3. Start with small risk settings in `paper_profile.json`

