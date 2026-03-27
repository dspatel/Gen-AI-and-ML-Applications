# Research Protocol

## Principle

The module must be testable under a true blind holdout regime.

No model, heuristic tuning, or rule refinement may use blind-test results to influence the chosen strategy.

## V1 Research Approach

Start with a rules-only baseline.

Do not train ML models before the rules-based baseline is fully specified and evaluated.

Implementation direction:

- research should run from DB-backed staging tables and run-scoped result tables
- CSV exports are allowed for review, but not as the primary source of truth

## Data Boundary

Current Alpaca historical options coverage begins in `February 2024`, so the initial research window is limited compared with equities.

That means our blind-test process must be especially disciplined.

Current historical ingestion implementation:

- historical underlying bars are pulled directly from Alpaca
- historical option bars are pulled directly from Alpaca
- contract metadata is pulled from Alpaca contracts
- event-time chain rows are currently reconstructed from option `1Min` bars plus underlying price
- historical delta and IV are currently stored as explicit Black-Scholes proxy estimates

This is acceptable for baseline research only if the approximation is kept visible in the data and docs.

It must not be mistaken for perfect historical NBBO reconstruction.

Current practical state:

- the original Alpaca historical blocker was resolved by querying expired contracts through `inactive` status during staging
- that means 2024-2025 train/validation windows can now be staged in this module using Alpaca alone
- the main remaining realism limitation is not contract discovery; it is quote/fill reconstruction from bar data rather than full historical NBBO

Current architecture step:

- the module routes historical staging through a provider abstraction keyed by `market_data.historical_provider`
- `alpaca` is the active implementation
- a second provider is no longer mandatory for current train/validation staging, but the abstraction remains in place if we later want richer quote history

## Proposed Temporal Split

Use contiguous time blocks, never randomized rows.

Initial recommendation:

- train:
  - `2024-02-01` through `2025-06-30`
- validation:
  - `2025-07-01` through `2025-12-31`
- blind test:
  - `2026-01-01` onward

The exact dates can be frozen once implementation starts, but the blind set must remain untouched until the strategy is locked.

Current implementation:

- split boundaries are configured in `research.splits`
- ranking policy is configured in `research.selection_policy`
- minimum support requirements are configured in `research.stability_gates`
- the sweep command is:
  - `python -m options_r6_stable.main protocol-sweep ...`
- if `research.blind_test_locked: true`, blind metrics are hidden unless:
  - `--reveal-blind`

Stability gates currently support:

- minimum train trades
- minimum validation trades
- minimum active days in each split
- minimum train profit factor
- minimum validation profit factor
- optional requirement that both train and validation net PnL stay positive

## What May Be Tuned

Tuning may use train and validation only.

Allowed tuning topics:

- DTE band
- delta band
- liquidity thresholds
- premium hard-stop threshold
- time-exit cutoff
- limited exit-variant comparisons

## What May Not Be Tuned On Blind Data

- entry thresholds
- contract rules
- exit thresholds
- risk sizing
- skip filters
- regime filters
- slippage assumptions

If blind data is consulted and then rules are changed, the blind set is no longer blind.

Operational rule:

- locked protocol sweeps should be the default workflow during tuning
- a reveal run should happen only when we are deliberately checking the frozen candidate
- revealed runs are stored separately in DB so we can audit when the blind set was first exposed

## Backtest Realism Requirements

The backtest must model:

- bid/ask spread
- entry slippage
- exit slippage
- unfilled and partially filled order outcomes
- chain liquidity filters
- time-of-day restrictions
- end-of-day forced liquidation rules

Current realism note:

- until historical quote-level chain snapshots are available, the current staging path uses a transparent bar-based execution proxy
- that means research results from this phase should be interpreted as `baseline directional-transfer validation`, not the final execution-quality benchmark

## Required Output Sets

For every frozen strategy run:

- run id
- config snapshot
- strategy version
- symbol list
- contract filter stats
- trades file
- daily returns file
- monthly returns file
- by-symbol performance
- by-direction performance
- blind-test summary

## Diagnostic Questions We Must Be Able To Answer

- Was the underlying signal wrong?
- Was the contract poorly chosen?
- Was the spread too wide?
- Was IV behavior hostile?
- Did time exit save or hurt the trade?
- Did the option fail because of execution rather than signal?

## ML Rule

No ML layer may be added until:

- rules-based baseline is implemented
- blind split is frozen
- baseline is evaluated
- diagnostics show what the baseline is missing

If ML is added later, it must still respect the original blind protocol.
