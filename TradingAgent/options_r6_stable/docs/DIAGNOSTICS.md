# Diagnostics

## Why This Exists

Options strategies can fail even when the directional idea was correct.

This document defines the questions the system must help answer.

## Root-Cause Categories

### Signal Failure

The underlying signal itself was wrong.

Evidence to inspect:

- underlying bar sequence
- reference-range state
- event timing
- immediate post-entry underlying move

### Contract Failure

The underlying moved correctly but the chosen option was poor.

Evidence to inspect:

- DTE too short or too long
- delta too low or too high
- spread too wide
- poor open interest or volume
- IV too elevated at entry
- historical chain proxy overstated tradability

### Execution Failure

The contract was acceptable but fills were poor.

Evidence to inspect:

- entry midpoint vs fill
- exit midpoint vs fill
- spread expansion during execution
- partial fills
- stale order timing
- broker order payloads and status transitions
- bar-based pricing proxy was too optimistic for what live quotes would have allowed

### Exit Failure

The signal was good but exit logic gave back too much.

Evidence to inspect:

- underlying exit trigger timing
- option premium path after peak
- forced exit timing
- premium hard-stop behavior

### Operational Failure

The system state or broker state was wrong.

Evidence to inspect:

- stale orders
- stale positions
- DB/broker mismatch
- missing EOD cleanup
- restart handling

## Minimum Diagnostic Outputs

Every trade review should be able to answer:

- what underlying event triggered the trade
- which contracts were considered
- why the selected contract was chosen
- how close the fill was to midpoint
- whether the contract stayed liquid during the trade
- whether the exit was signal-driven, risk-driven, or operational
- how PnL evolved while the trade was open
- whether IV or spread deterioration hurt the result

## Where To Look

Primary tables and views for investigation:

- `underlying_r6_signals`
- `option_chain_snapshots`
- `option_contract_candidates`
- `selected_option_contracts`
- `options_orders`
- `options_position_marks`
- `options_trades`
- `options_decision_steps`
- `options_events`
- `options_missed_trades`
- `options_portfolio_snapshots`
- `vw_options_trade_research`
- `vw_options_order_audit`

When the trade came from Alpaca historical staging, inspect:

- `research_input_signals`
- `research_input_chain_snapshots`
- `research_input_outcomes`

especially the `source_tag` and `notes_json` fields, because they tell us whether the row came from:

- direct sample fixtures
- Alpaca historical option-bar staging
- a future higher-fidelity quote-level staging path

These should answer most research and operational questions without forcing manual log reconstruction.

## Expected Postmortem Flow

When a trade underperforms, the default review path should be:

1. Inspect `vw_options_trade_research`
2. Confirm the underlying setup in `underlying_r6_signals`
3. Review rejected and accepted contracts in `option_contract_candidates`
4. Inspect order lifecycle in `vw_options_order_audit`
5. Inspect intratrade path in `options_position_marks`
6. Check structured decisions in `options_decision_steps`
7. Check runtime warnings in `options_events`

## Daily Review Questions

At the end of each day we should be able to review:

- Did the system skip good trades because of over-strict filters?
- Did the system take poor trades because filters were too loose?
- Were losers mostly signal failures or contract failures?
- Were wins large enough to justify spread/slippage costs?
- Did any symbol behave materially worse than the rest?
- Did calls and puts behave differently under the same underlying conditions?
- Did portfolio exposure become too concentrated by symbol or direction?
