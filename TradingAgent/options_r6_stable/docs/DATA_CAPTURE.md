# Data Capture

## Purpose

This module is intentionally designed to over-capture trade context.

The reason is simple:

- options failures are often not explained by entry and exit prices alone
- future research depends on reconstructing both the decision and the path

## Design Rule

Default behavior:

- store normalized fields that will be queried often
- also store raw or semi-raw JSON context when a future question is hard to predict
- do not discard context just because it is not used in the first reporting layer
- prefer DB staging tables for working research state instead of file-based intermediates

The system should prefer reversible storage over aggressive compression of decision context.

## Minimum Capture Layers

### Underlying Layer

Capture:

- full underlying event timestamp
- underlying bar OHLC at event
- reference-range values
- horizon and phase
- confidence and raw signal payload
- signal age at the time of decision
- underlying eligibility state
- provider timestamp, ingest timestamp, and whether the bar was complete

### Chain Layer

Capture:

- full qualifying chain snapshot time
- bid, ask, mid, spread percent
- delta, IV, open interest, volume
- bid size and ask size when available
- intrinsic and extrinsic value when available
- contract metadata such as right, strike, expiration, DTE
- raw source payload if available
- chain batch metadata for each fetch event

For the current Alpaca historical staging path, also capture:

- whether chain pricing came from a direct snapshot or a bar-based proxy
- the proxy method used for historical bid/ask reconstruction
- whether IV and delta are direct vendor fields or derived estimates
- the provenance of open-interest and liquidity fields

### Selection Layer

Capture:

- every contract candidate considered
- pass/fail result per filter
- reject reason
- ranking score
- why the selected contract beat the alternatives
- filter flags and scoring context
- final limit price intent used for execution

### Execution Layer

Capture:

- submitted order parameters
- broker order ids
- client order ids
- order status transitions
- fills and partial fills
- fill price vs midpoint
- entry and exit slippage estimates
- parent/child order relationships when used
- broker transition payloads across the whole order lifecycle

### Position Path Layer

Capture:

- time-series marks while open
- unrealized PnL path
- delta and IV path if available
- underlying price path while the option is open
- theta, vega, and spread percent when available
- latest mark timestamp and running extrema

### Exit Layer

Capture:

- exit reason
- underlying state at exit
- option state at exit
- trade duration
- MFE and MAE style diagnostics
- profit-lock state if one was active
- explicit exit signal payload when available

### Portfolio Layer

Capture:

- account cash
- buying power
- portfolio value
- options market value
- realized and unrealized PnL
- open positions and open orders count
- exposure snapshot by symbol and direction

### Audit Layer

Capture:

- structured decision steps
- warnings and operational events
- missed trades with reason and stage
- daily metrics and end-of-day rollup context

## Research Views

The database should expose research-friendly joined views in addition to raw tables.

Primary examples:

- `vw_options_trade_research`
- `vw_options_order_audit`

These are intended to reduce time spent rebuilding joins during postmortems.

## Why This Matters

If a trade loses money, we need to know whether it failed because:

- the underlying signal was wrong
- the chosen contract was poor
- the fill was poor
- the exit logic was poor
- the broker/runtime behavior was wrong

It is also how we separate:

- a bad signal
- a bad contract
- a good signal expressed through an overly optimistic historical proxy

That is only possible if the data was captured at each stage.

## Rule

If a future implementation choice would save effort by dropping useful trade context, prefer keeping the context unless the storage cost is clearly unreasonable.
