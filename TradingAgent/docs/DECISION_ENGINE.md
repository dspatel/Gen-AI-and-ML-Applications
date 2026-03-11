# Decision Engine

## Layers

1. Data layer
   - Signal bars: Yahoo 5m (paper profile default)
   - Selection backfill bars: Alpaca (paper profile default)
2. Selection layer
   - Monthly/quarterly reselection
   - Prior-data-only split
3. Execution layer
   - Entry detection
   - Order placement
   - Stop/exit maintenance

## Selection logic summary

For each symbol:

1. Build candidate strategy performance over lookback window
2. Split into train and validation subwindows
3. Enforce minimum train/validation trade counts
4. Mark strict pass when:
   - train return > 0 and validation return > 0
   - train PF >= 1 and validation PF >= 1
   - train avg R > 0 and validation avg R > 0
5. Rank by weighted score prioritizing strict/eligible/stability/validation
6. Persist best strategy as active map row

## Live execution logic summary

For each symbol each cycle:

1. Load active strategy spec
2. If open position exists:
   - evaluate exits on new bars only
   - update trailing stop state
   - close when exit condition hits
3. Else:
   - evaluate latest ORB confirmation signal
   - apply trade limit and side mode filters
   - compute quantity from risk and notional caps
   - place paper order (if non-dry)
   - register position state in DB

## Broker reconciliation

If DB says a position is open but broker reports no open position, the engine closes DB state with reason `BROKER_SYNC_FLAT`.

