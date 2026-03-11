# Strategy Schema

## Session and market structure

- Timezone: `America/Chicago`
- Session: `08:30-15:00`
- Opening range window: `08:30-09:00`

## Entry model

For each symbol and timeframe (`5m` or `15m`):

1. Compute OR high/low from first 30 minutes
2. Scan post-OR bars up to `14:45`
3. Trigger LONG when:
   - previous close > OR high
   - current close > OR high
   - prior-2 close <= OR high
4. Trigger SHORT symmetrically below OR low
5. Entry price = confirmation bar close
6. Initial stop:
   - LONG: min(prev low, current low) - 0.01
   - SHORT: max(prev high, current high) + 0.01

## Exit model

Live-supported exits:

- `FIXED_2R`
- `EMA20_TRAIL`
- `TIME_STOP_NO_PROGRESS`
- `OR_REENTRY_FAIL`
- `BREAKEVEN_RATCHET`
- `STACK_*` (composed from TSNP, ORRF, BER)

## Sizing

Per trade quantity is bounded by:

1. Risk budget:
   - `equity * risk_pct_per_trade / abs(entry - stop)`
2. Max notional budget:
   - `(equity * max_notional_pct) / entry`

Quantity = `floor(min(risk_qty, notional_qty))`

## Reselect schema

Reselection chooses one active strategy per symbol using:

- prior-data-only lookback window
- train segment and validation segment
- minimum train/validation trades
- rank score based on eligibility, strict pass, stability, validation metrics

