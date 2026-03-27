# Success Metrics

## Purpose

Define what success means before any research tuning begins.

This prevents the strategy from being "optimized" into whatever happened to work in a short sample.

## Portfolio-Level Objective

Target aspiration:

- `1% to 2%` daily portfolio return on strong market sessions

Important interpretation:

- This is a portfolio objective, not a guaranteed daily output
- It must not be converted into forced daily trade behavior

## Minimum Research Acceptance Gates

These gates should be evaluated on the blind test set, not on training data.

### Blind-Test Return Quality

- positive total return
- positive average daily expectancy
- profit factor above `1.10`

### Trade Quality

- positive average trade expectancy
- positive median trade expectancy
- acceptable slippage sensitivity under conservative assumptions

### Stability

- no single symbol should explain almost all returns
- no single day should dominate results
- no single contract rule should drive all performance

### Risk

- capped maximum single-trade loss under logged assumptions
- acceptable max drawdown
- acceptable losing streak length
- no evidence that returns disappear once realistic spread/slippage is applied

## Operational Success Metrics

The live paper system should also satisfy:

- deterministic contract selection
- correct entry/exit audit trail
- no stale trades after restart
- no expiry carry unless explicitly configured
- no silent skipped trades
- no silent fallback to invalid data

## Diagnostics We Must Always Report

- total trades
- win rate
- profit factor
- average return per trade
- average and median premium loss on losers
- average and median premium gain on winners
- max drawdown
- exposure by symbol
- exposure by direction
- spread cost estimate
- slippage estimate
- missed trade counts by reason
- rejected contract counts by reason

## Failure Thresholds

The following are warning signs even if raw return looks attractive:

- return depends on one symbol only
- return depends on a tiny number of trades
- performance collapses after realistic costs
- large positive gross return but poor realized fills
- strong train/validation but weak blind test
- blind-test behavior materially worse than validation

## Decision Rules

If blind-test behavior fails risk or realism checks:

- do not move to live paper
- do not add ML
- diagnose signal quality, contract selection, and fill assumptions first
