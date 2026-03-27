# Strategy Spec

## Strategy Thesis

When the underlying emerges from a compressed multi-session reference regime and confirms directional expansion, long premium can provide asymmetric upside with capped loss.

The options module will not infer direction from options data. Direction comes from the underlying. Options are the trade expression layer.

## Underlying Signal Layer

The planned underlying trigger is based on the R6 family:

- Use `15m` bars on the underlying
- Use the same reference-range framework as equity R6
- Use the underlying breakout event for direction and timing
- Use only completed bars

Planned v1 baseline:

- Variant family anchored to `R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY`
- Both bullish and bearish directions allowed
- One active options position per symbol at a time

## Direction Mapping

- Bullish underlying event -> buy `call`
- Bearish underlying event -> buy `put`

## Contract Selection

V1 should favor simplicity, liquidity, and repeatability.

### Direction Filter

- Calls only for bullish signals
- Puts only for bearish signals

### Expiration Filter

Allowed DTE band:

- Minimum: `14`
- Maximum: `21`

Reason:

- Avoid extreme theta of very near expiry
- Keep option sensitive enough for intraday moves
- Avoid overpaying for too much time value

### Delta Filter

Target band:

- `0.40` to `0.60`

Selection preference:

- Choose the qualifying contract closest to `0.50 delta`

Reason:

- More robust than arbitrary strike distance
- Keeps direction sensitivity meaningful
- Avoids lottery-style far OTM contracts
- Avoids expensive deep ITM contracts in v1

### Liquidity Filter

Do not trade contracts that fail any of:

- minimum open interest
- minimum contract volume
- maximum spread percent
- valid bid and ask
- non-zero midpoint

If no contract passes filters:

- skip the trade
- log the rejection reason

## Position Sizing

The risk budget should be based on premium-at-risk, not stock notional.

V1 sizing controls:

- maximum risk percent of portfolio
- maximum dollar premium risk
- symbol-specific premium-risk overrides for larger liquid names
- maximum contracts per trade
- maximum total open premium exposure
- maximum per-symbol open premium exposure
- maximum same-direction open premium exposure
- minimum cash reserve
- daily realized-loss stop for new entries
- maximum new trades per day

V1 default recommendation:

- 1 contract max until live paper behavior is understood

Reason:

- Prevent hidden leverage from distorting early evaluation

Portfolio guardrails:

- the engine should never allocate all available cash to one options trade
- the effective trade budget is the minimum of:
  - per-trade premium cap
  - remaining total open-premium capacity
  - remaining symbol-level premium capacity
  - remaining direction-level premium capacity
  - deployable cash after reserve

Symbol override rule:

- some names such as `SPY` may require a higher premium budget than the default cap
- when a symbol-specific override is configured, the strategy uses that override for the per-trade premium cap
- because v1 allows only one active position per symbol, the symbol-level open-premium cap is also aligned upward to at least that override

If any of those limits is exhausted:

- skip the trade
- log the rejection reason and budget context

## Entry Mechanics

### Entry Trigger

Use the underlying event timestamp, not option-chain movement, as the trigger.

### Entry Order Style

V1 should prefer `limit orders`, not blind market orders.

Suggested rule:

- buy at a limit derived from option midpoint with a bounded tolerance

Reason:

- option spreads can be wide
- market orders can introduce misleading slippage

### Entry Validity Window

If the selected contract no longer passes spread or liquidity checks at submission time:

- skip the trade
- log a missed-trade reason

## Exit Logic

The options exit should be a hybrid, not a copy of equity R6.

### Why Not Copy Equity R6 Exactly

Option premium is affected by:

- underlying move
- delta
- implied volatility
- time decay
- spread behavior

So a pure stock-trailing exit is not sufficient by itself.

### Planned Hybrid Exit

1. Underlying invalidation exit
   - if the underlying trade thesis is invalidated, exit the option
2. Premium hard-loss guard
   - if premium loss breaches the allowed threshold, exit
3. Time exit
   - force exit before end of session
4. Optional profit-protection layer
   - later research candidate, not mandatory in initial baseline

### V1 Time Handling

- Same-day exit only
- Force exit before close, for example `14:50 CT`
- No intentional overnight holds

## Why Same-Day Exit First

- Lower overnight gap risk
- Lower theta drift risk
- Lower exercise/assignment complexity
- Easier to compare paper behavior with underlying event timing

## Planned Research Variants

The first research branch should compare a small set only:

1. Underlying invalidation + time exit
2. Underlying invalidation + premium hard stop + time exit
3. Underlying invalidation + premium hard stop + early profit lock + time exit

Avoid large variant grids before the baseline is validated.

## Symbol Considerations

More stable anchors:

- `SPY`
- `AAPL`
- `MSFT`

Higher-motion names with stricter liquidity needs:

- `NVDA`
- `TSLA`
- `AMD`

Middle group:

- `AMZN`
- `GOOGL`
- `META`

## Planned Operational Constraints

- no new entries near forced exit cutoff
- no overlapping positions on the same symbol
- skip contracts with poor spread quality
- no trading when chain snapshot is incomplete
- no trading when contract metadata is stale
