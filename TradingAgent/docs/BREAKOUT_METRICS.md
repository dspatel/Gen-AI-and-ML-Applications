# Breakout Metrics

## Signal quality fields (derived in strategy logic)

- Confirmation type:
  - long breakout above OR high
  - short breakout below OR low
- Entry timestamp and price
- Initial stop and risk per share

## Exit behavior markers

Live/research exits emit explicit reasons such as:

- `STOP`
- `TARGET_2R`
- `TRAIL_STOP`
- `TIME_EXIT`
- `TIME_STOP_NO_PROGRESS`
- `OR_REENTRY_FAIL`
- `BREAKEVEN_TRAIL_STOP`
- `BROKER_SYNC_FLAT` (live reconciliation path)

## Practical use

Use exit reason distributions to evaluate:

1. Premature exits
2. Stop efficiency
3. Time-stop sensitivity by timeframe
4. Strategy robustness by symbol

