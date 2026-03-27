# Usage

## Current State

This folder contains:

- design documentation
- standalone config
- standalone DB schema initializer
- standalone Alpaca health check
- standalone deterministic contract-selection demo
- standalone DB-first sample research replay
- standalone Alpaca historical event staging into DB tables

It does not yet contain the full research engine or paper-trading engine.

## Intended Commands

Planned commands after implementation:

- research:
  - `python -m agent.main --mode options_r6_research --config .\options_r6_stable\config\config.yaml`
- paper live:
  - `python -m agent.main --mode options_r6_paper --config .\options_r6_stable\config\config.yaml`
- diagnose:
  - `python -m agent.main --mode options_r6_diagnose --run-id <RUN_ID>`

## Planned Inputs

- underlying intraday bars
- option chain snapshots
- option quote data
- broker paper account state

## Working Storage Policy

- database tables are the primary working storage
- CSV is treated as an export or interchange format, not the core runtime state
- sample research inputs are seeded into DB tables before replay

## Planned Outputs

- strategy summary JSON
- trade ledger CSV
- daily metrics CSV
- diagnostic report JSON
- end-of-day Discord trade report

## Available Scaffold Commands

- `python -m options_r6_stable.main describe-config`
- `python -m options_r6_stable.main init-db`
- `python -m options_r6_stable.main doctor`
- `python -m options_r6_stable.main plan-demo --chain-csv .\options_r6_stable\config\sample_chain.csv --symbol SPY --direction BULLISH --event-ts 2026-03-15T10:00:00-05:00`
- `python -m options_r6_stable.main record-demo --chain-csv .\options_r6_stable\config\sample_chain.csv --symbol SPY --direction BULLISH --event-ts 2026-03-15T10:00:00-05:00 --underlying-price 667.10 --underlying-stop 661.50`
- `python -m options_r6_stable.main seed-sample-research`
- `python -m options_r6_stable.main seed-r6-signals --start 2026-03-09 --end 2026-03-13`
- `python -m options_r6_stable.main stage-historical-event --symbol SPY --direction BULLISH --event-ts 2026-03-13T10:00:00-05:00 --underlying-stop 661.50`
- `python -m options_r6_stable.main stage-historical-signals --start 2026-03-01 --end 2026-03-15`
- `python -m options_r6_stable.main stage-alpaca-event --symbol SPY --direction BULLISH --event-ts 2026-03-13T10:00:00-05:00 --underlying-stop 661.50`
- `python -m options_r6_stable.main stage-alpaca-signals --start 2026-03-01 --end 2026-03-15`
- `python -m options_r6_stable.main research-replay --start 2026-01-01 --end 2026-01-31 --starting-equity 100000 --run-label sample_blind`
- `python -m options_r6_stable.main protocol-sweep --start 2024-02-01 --end 2025-12-31 --label train_validate_locked`
- `python -m options_r6_stable.main protocol-sweep --start 2024-02-01 --end 2026-03-31 --label reveal_once_frozen --reveal-blind`

Historical staging note:

- use `stage-historical-*` for the provider-agnostic path
- the older `stage-alpaca-*` commands are still available as compatibility aliases
- the active provider is controlled by `market_data.historical_provider` in config

## What `record-demo` Does

It persists a full demo decision path into the standalone DB:

- underlying signal row
- option chain batch and snapshots
- candidate contract evaluations
- selected contract row if one passes
- structured decision steps
- planned position row if sizing succeeds
- missed-trade row if no trade is taken
- audit event rows

This is the first capture-first path and is meant to prove the research schema before the full research engine is built.

## What `seed-sample-research` Does

It seeds DB-backed research input tables:

- `research_input_signals`
- `research_input_chain_snapshots`
- `research_input_outcomes`

These are fixtures for validating the research replay flow without depending on live or historical API pulls yet.

## What `seed-r6-signals` Does

It seeds `research_input_signals` from the existing standalone R6 equity research database:

- reads R6 breakout events from `artifacts/r6_stable/orb_core.sqlite`
- applies the configured variant policy:
  - confidence threshold
  - one-trade-per-symbol-per-day rule
  - `NO_LONG_PREOR`
  - `FLAT_ONLY` if present in the variant id
- converts accepted R6 events into options-research input rows
- computes an underlying stop based on the R6 breakout candle so options replay has a structural invalidation anchor

Use it before `stage-alpaca-signals` when you want the options module to inherit real underlying signal timing from the equity R6 research stream.

## What `research-replay` Does

It reads from the DB-backed research input tables and writes run-scoped research artifacts into the core options tables:

- captures the full signal and contract-selection path
- simulates filled entry and exit orders from stored outcomes
- closes trades into `options_trades`
- stores run-scoped daily metrics
- writes convenience exports under `artifacts/options_r6_stable/research/<RUN_ID>/`

Current behavior:

- replay now tracks overlapping open option positions
- open-premium exposure is carried forward until each research trade's staged `exit_ts`
- cash available for the next signal is reduced by still-open premium at risk
- same-direction and portfolio-level premium caps therefore behave much closer to a real session

## What `protocol-sweep` Does

It runs the contract-variant and exit-variant sweep under the configured temporal split policy:

- loads staged research signals from DB
- evaluates each contract and exit variant combination
- aggregates separate summaries for:
  - `train`
  - `validation`
  - `blind`
  - `all`
- ranks variants using `research.selection_policy`
- keeps blind hidden when `research.blind_test_locked: true` unless `--reveal-blind` is passed
- persists results in DB-first form:
  - `options_protocol_runs`
  - `options_protocol_results`
- writes a JSON artifact under:
  - `artifacts/options_r6_stable/research/protocol_sweeps/`

Practical use:

- use locked mode while refining rules
- reveal blind once we intentionally freeze the candidate strategy
- do not change rules after a reveal run without resetting the blind protocol

## What `stage-alpaca-event` Does

It stages one real Alpaca-backed historical signal into DB tables:

- fetches historical `15Min` underlying stock bars from Alpaca
- stores them in `underlying_bars_intraday`
- enriches the signal with event-bar OHLC and EMA20 context
- lists eligible option contracts in the configured DTE band
- fetches historical `1Min` option bars for those contracts
- reconstructs event-time chain rows in `research_input_chain_snapshots`
- stores event-forward option bar paths in `research_input_option_bar_paths`
- stores event-forward underlying `1Min` paths in `research_input_underlying_bar_paths`
- generates per-contract research outcomes in `research_input_outcomes`

This is the current bridge from sample fixtures to real historical research.

## What `stage-alpaca-signals` Does

It takes already-seeded rows from `research_input_signals` and stages Alpaca-backed chains and outcomes for them in bulk.

Use it when:

- you already inserted or generated signal rows
- you want to build research inputs in DB without CSV intermediates

## Current Historical Approximation

Alpaca historical options data gives us:

- contract metadata
- historical option bars
- historical underlying stock bars

It does not currently give us full historical chain snapshots with greeks at arbitrary past timestamps through this implementation path.

So the current staging logic is explicit about its proxy model:

- event-time option pricing uses the next available `1Min` option bar open
- historical bid/ask are proxied from that bar price for staging
- delta and IV are derived with a transparent Black-Scholes proxy
- current/latest open interest metadata is stored with provenance notes

These approximations are recorded in `source_tag` and `notes_json` so later research can separate:

- signal quality
- contract-quality proxy risk
- execution-model risk

The staged option-bar path table is the foundation for honest exit testing:

- take-profit thresholds can be evaluated against bars that were actually available after entry
- replay does not need to infer intratrade behavior from only peak/trough summary fields
- this reduces lookahead risk when we test early profit-taking rules

The staged underlying-path table is the foundation for behavior-layer exits:

- no-progress / stall exits can inspect the underlying after entry
- momentum-decay research can use the same event-forward bar path every run
- behavior-aware experiments stay DB-first and deterministic

## Current Practical Constraint

With the current config:

- DTE `14-21`
- delta `0.40-0.60`
- premium-risk cap `$500`

some symbols can select valid contracts but still fail position sizing because one contract costs more than the allowed premium budget.

That is now visible during replay as:

- `position_sizing -> premium_budget_too_small_for_selected_contract`

## Planned Config Controls

- symbol universe
- option DTE range
- delta target band
- liquidity filters
- spread tolerance
- premium risk per trade
- total open premium exposure
- per-symbol premium exposure
- same-direction premium exposure
- minimum cash reserve
- daily loss stop for new entries
- maximum new trades per day
- force-exit time
- blind-test boundaries
- selection policy for train vs validation ranking
- symbol-specific premium-risk overrides for larger names like `SPY`

## Current Symbol-Aware Risk Behavior

The module now supports symbol-level premium-risk overrides through:

- `options.symbol_risk_overrides`

Each override can set:

- `max_premium_risk_per_trade_pct`
- `max_premium_risk_per_trade_dollars`

Current intent:

- keep the default risk cap conservative
- allow large liquid names such as `SPY` to participate without loosening the entire universe

Implementation detail:

- when a symbol override is active, the per-symbol open-premium cap is aligned to at least that symbol's per-trade cap
- this matters because the module currently permits only one active options position per symbol
- without that alignment, a valid symbol-level trade cap could still be blocked by a lower generic symbol exposure limit
