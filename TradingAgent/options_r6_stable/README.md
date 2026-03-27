# Options R6 Stable

Standalone workspace for the planned options agent that will reuse the R6-style underlying signal logic while remaining fully isolated from ORB and equity R6.

Status:

- Design-first build with working research staging
- Standalone scaffold code added
- Capture-first research schema in place
- DB-first sample research replay available
- Real Alpaca event staging available for historical stock bars, option bars, and contract metadata
- No production trading engine yet
- No shared live state with ORB or R6

Why this folder exists:

- Options trading needs its own contract selection, risk, fill, and expiry logic
- We want a clean research boundary with true blind testing
- We do not want experiments here to affect existing agents

Planned folder scope:

- `config/`: future runtime and research configs
- `docs/`: strategy, testing, schema, runbook, and diagnostics docs
- `artifacts/`: future isolated outputs, reports, DBs, and trade logs
- Python package files in the folder root

Current scaffold commands:

- `python -m options_r6_stable.main describe-config`
- `python -m options_r6_stable.main doctor`
- `python -m options_r6_stable.main init-db`
- `python -m options_r6_stable.main plan-demo --chain-csv <CSV> --symbol SPY --direction BULLISH --event-ts 2026-03-15T10:00:00-05:00`
- `python -m options_r6_stable.main record-demo --chain-csv <CSV> --symbol SPY --direction BULLISH --event-ts 2026-03-15T10:00:00-05:00 --underlying-price 667.10 --underlying-stop 661.50`
- `python -m options_r6_stable.main seed-sample-research`
- `python -m options_r6_stable.main seed-r6-signals --start 2026-03-09 --end 2026-03-13`
- `python -m options_r6_stable.main stage-historical-event --symbol SPY --direction BULLISH --event-ts 2026-03-13T10:00:00-05:00 --underlying-stop 661.50`
- `python -m options_r6_stable.main stage-historical-signals --start 2026-03-01 --end 2026-03-15`
- `python -m options_r6_stable.main stage-alpaca-event --symbol SPY --direction BULLISH --event-ts 2026-03-13T10:00:00-05:00 --underlying-stop 661.50`
- `python -m options_r6_stable.main stage-alpaca-signals --start 2026-03-01 --end 2026-03-15`
- `python -m options_r6_stable.main research-replay --start 2026-01-01 --end 2026-01-31 --starting-equity 100000 --run-label sample_blind`
- `python -m options_r6_stable.main protocol-sweep --start 2024-02-01 --end 2025-12-31 --label train_validate_locked`
- `python -m options_r6_stable.main protocol-sweep --start 2024-02-01 --end 2026-03-31 --label reveal_once_frozen --reveal-blind`

Core design choice:

- Use R6-style logic on the underlying for direction and timing
- Express that view through long options only in v1
- Keep options logic separate from equity logic

Data policy:

- keep normalized research fields for fast SQL analysis
- keep raw or semi-raw JSON context for future diagnosis
- avoid dropping trade context early just because the first report does not need it
- use DB tables as the working storage layer; treat CSV primarily as export, not core state

Current historical-data implementation note:

- Alpaca gives us historical option bars and contract metadata, but not full historical chain snapshots with greeks at arbitrary past timestamps
- the current staging path reconstructs event-time contract state from option `1Min` bars plus the underlying bar
- delta and IV are stored as explicit `Black-Scholes proxy` estimates and marked in `notes_json`
- bid/ask are currently proxied from the entry minute open for historical staging, and the provenance is kept in the DB so we can later tighten the realism model instead of hiding the approximation

Historical provider abstraction:

- research staging is now configured through `market_data.historical_provider`
- `alpaca` is the only implemented historical provider today
- historical staging now supports expired-contract discovery through Alpaca `inactive` contract lookup
- the abstraction layer is still useful if we want a second archive source later, but Alpaca is now sufficient for current 2024-2026 research staging in this module
- generic commands `stage-historical-event` and `stage-historical-signals` now route through the configured historical provider

Protocol-sweep workflow:

- split boundaries now live in `research.splits` in config
- `protocol-sweep` ranks variants on `train` and `validation` only using `research.selection_policy`
- if `research.blind_test_locked: true`, blind metrics stay hidden unless `--reveal-blind` is passed
- locked runs and revealed runs are both persisted in DB so we can audit when blind was first exposed
- results are stored in:
  - `options_protocol_runs`
  - `options_protocol_results`

Current Alpaca historical note:

- the original research blocker was largely in our staging logic, not in Alpaca’s raw option-bar availability
- expired historical contracts are now discovered by explicitly querying `inactive` Alpaca contracts for past expiration windows
- this unlocks train/validation staging across our current 2024-2025 research windows
- remaining realism caveat:
  - fills and quotes are still reconstructed from historical bars, not full historical NBBO snapshots

Current research finding from live staging:

- with the current `14-21 DTE`, `0.40-0.60 delta`, and `$500` premium-risk cap, some symbols in the target universe can fail position sizing even after a valid contract is selected
- this is now observable in DB-backed replay rather than being guessed

Current replay realism note:

- research replay now accounts for overlapping open option positions when multiple same-day signals fire
- cash-available and open-premium checks are based on still-open positions, not a reset-to-zero assumption on each signal
- this makes short-window research less flattering but more realistic

Risk policy:

- cap premium at risk per trade
- allow symbol-specific per-trade premium overrides for large, liquid names
- cap premium exposure at portfolio, symbol, and direction levels
- preserve a cash reserve instead of consuming all available cash

Current implementation note:

- symbol-specific trade-cap overrides are enabled for the core live universe
- for a symbol override, the per-symbol open-premium cap is automatically aligned to at least that per-trade cap
- this prevents contradictory settings where a symbol is allowed per-trade but blocked by a lower symbol-level cap

Primary docs:

- `docs/PROJECT_BLUEPRINT.md`
- `docs/DATA_CAPTURE.md`
- `docs/STRATEGY_SPEC.md`
- `docs/SUCCESS_METRICS.md`
- `docs/RESEARCH_PROTOCOL.md`
- `docs/SCHEMA.md`
- `docs/RUNBOOK.md`
- `docs/USAGE.md`
- `docs/NOTIFICATION_FORMAT.md`
- `docs/DIAGNOSTICS.md`
