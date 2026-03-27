# Project Blueprint

## Objective

Build a standalone options trading research and paper-trading module that reuses the R6-style underlying signal logic, but applies separate options contract selection, execution, risk, and exit rules.

Primary goal:

- Directional long-premium options trading on high-liquidity names

Portfolio objective:

- Target `1% to 2%` daily portfolio return on strong sessions without forcing trades on weak sessions

Important constraint:

- Portfolio target is an outcome goal, not a training label or a sizing promise

## Why R6 Is The Starting Point

The base attraction of R6 is not that it is an options strategy. It is that R6 has shown desirable behavior on the underlying:

- Entries are directionally meaningful
- Exits are disciplined
- Losses are usually contained when fills behave correctly
- The logic is explainable and auditable

That makes R6 a reasonable underlying signal source for an options module.

## What This Agent Is

- A new module in a new folder
- Separate config, DB, docs, outputs, and paper state
- Uses the underlying to generate the directional signal
- Uses options contracts to express the trade

## What This Agent Is Not

- Not a copy of equity R6
- Not a direct application of R6 to option candles
- Not a multi-leg or short-options system in v1
- Not an ML-first system in v1
- Not a sentiment-driven system in v1

## V1 Scope

- Symbols:
  - `SPY`, `AAPL`, `NVDA`, `TSLA`, `MSFT`, `AMZN`, `GOOGL`, `META`, `AMD`
- Long premium only:
  - buy calls on bullish signals
  - buy puts on bearish signals
- Intraday only in v1:
  - enter during the session
  - force exit before the close
- One standalone options module
- Paper trading first
- DB-first storage
- Full trade and event audit trail

## Non-Goals For V1

- No short calls
- No short puts
- No credit spreads
- No debit spreads
- No 0DTE specialization
- No holding through expiration
- No hidden adaptive model logic

## Design Principles

- Keep the underlying signal logic interpretable
- Keep contract selection deterministic
- Keep risk capped by premium paid
- Keep exits explainable
- Log every important decision needed for future diagnosis
- Protect a truly blind test set from all tuning decisions

## Required Workstreams

1. Strategy definition
2. Contract selection rules
3. Options-specific risk model
4. Backtest/research engine
5. Paper-trading execution engine
6. Logging and diagnostics
7. Blind-test evaluation
8. Documentation

## Core Decision Chain

The planned live decision chain is:

1. Build or ingest underlying bars
2. Compute R6-style underlying signal state
3. Generate a directional event on the underlying
4. Check trade eligibility:
   - session window
   - symbol universe
   - no active position conflict
   - daily risk budget
   - event and liquidity filters
5. Pull option chain snapshot
6. Filter contracts by direction, DTE, delta, liquidity, and spread
7. Select the best qualifying contract
8. Size by premium-at-risk budget
9. Submit paper order
10. Manage position with hybrid exit logic
11. Persist all lifecycle rows and diagnostics
12. Produce end-of-day summaries

## Main Risks We Must Design Around

- Wrong directional signal
- Good directional signal but bad contract choice
- Wide spread and bad fills
- IV crush after entry
- Contract too close to expiration
- Insufficient liquidity on fast-moving names
- Partial fills at entry or exit
- Restart / stale-order issues
- Overnight exercise/assignment risk if exit rules fail

## Planned Deliverables

- Standalone code module
- Standalone SQLite DB
- Research outputs
- Paper-trading runner
- End-of-day trade reports
- Discord notifications
- Detailed docs for strategy, testing, diagnostics, schema, and operations
