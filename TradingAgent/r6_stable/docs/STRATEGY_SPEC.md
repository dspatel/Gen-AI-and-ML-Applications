# Strategy Specification

## Session and timeframe

- Timezone: `America/Chicago`
- Session: `08:30` to `15:00`
- Intraday engine timeframe: `15m`
- Reference horizons: `3`, `5`, `9` sessions

## Reference range model

For each horizon `H`:

1. Take last `H` prior sessions' OR bands (`or_low`, `or_high`).
2. Compute:
- `ref_high = max(or_highs)`
- `ref_low = min(or_lows)`
- `ref_width = ref_high - ref_low`
3. Compute quality/regime context:
- `inflation_factor = ref_width / median(or_width)`
- overlap/bias/stability metrics.

Phase handling:

- `include_today_or = 0` pre-OR
- `include_today_or = 1` post-OR

## Breakout event and decision

Breakout detection (close-only):

- UP if `close > ref_high`
- DOWN if `close < ref_low`

Primary horizon:

- smallest horizon that breaks on that bar (usually `H=3`).

Decision score inputs:

- breakout strength, wick/close penetration, OR overlap, inflation, directional bias, confluence, pre/post-OR phase.

Decision thresholds:

- `confidence >= 0.62`: trade allowed
- `0.54 <= confidence < 0.62`: low conviction zone
- `< 0.54`: no trade

## Selected stable variant

- `R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY`

Entry filters:

1. Confidence >= `0.62`
2. One trade max per symbol/day
3. No long entries from pre-OR phase (`include_today_or=0`)

Entry execution:

- enter at next bar open after signal.

Stop:

- structural stop from signal candle extreme with minimum distance guard.

Exit:

- `EMA20_TRAIL_ONLY`
- LONG: trail = max(previous trail, `ema20_prev`), exit if `low <= trail`
- SHORT: trail = min(previous trail, `ema20_prev`), exit if `high >= trail`
- if not stopped, exit at EOD.

## Stability profile (latest full run)

Run: `52dc3d23-4462-4b78-a254-885b4c821fe2`

Yearly returns:

- 2023: `+47.15%`
- 2024: `+61.04%`
- 2025: `+39.29%`
- 2026 YTD: `-3.50%`

Horizon trade mix:

- H3: `3354`
- H5: `87`
- H9: `16`

## Paper-trading readiness status

`r6_paper` is now available for end-to-end paper execution:

1. signal generation from R6 breakout engine
2. entry filters for selected strategy (`CONF62`, `LIMIT1`, `NO_LONG_PREOR`)
3. Alpaca paper market orders for entries/exits
4. EMA20 trail and EOD time exit
5. trade/position/event persistence in SQLite

Recommended hardening before real money:

1. broker-state reconciliation hardening across restarts/outages
2. protective stop orders at broker level (currently strategy-managed exits)
3. portfolio kill-switches (daily loss limit, symbol correlation caps)
4. fill/slippage model calibration versus broker fills.
