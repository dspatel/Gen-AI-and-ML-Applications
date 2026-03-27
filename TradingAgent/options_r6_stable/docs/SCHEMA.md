# Schema

## DB Philosophy

- SQLite first
- deterministic replay
- one standalone DB for this module only
- no shared runtime tables with ORB or R6
- capture-first schema, even if some fields are not used in the earliest execution path

## Capture Policy

For every trade, the module should preserve:

- the underlying event context
- the option chain snapshot used at decision time
- every contract candidate considered
- the exact contract finally selected
- the order lifecycle
- the mark-to-market path while open
- the final exit state
- enough JSON context to reconstruct why the engine behaved the way it did

## Planned Tables

### `research_input_signals`

Purpose:

- DB-first staging table for offline research signal inputs

Suggested columns:

- `event_id`
- `symbol`
- `session_date`
- `event_ts`
- `direction`
- `variant_id`
- `confidence`
- `ref_horizon`
- `include_today_or`
- `underlying_price`
- `underlying_stop_price`
- `bar_open`
- `bar_high`
- `bar_low`
- `bar_close`
- `ema20`
- `ema20_slope`
- `source_tag`
- `notes_json`
- `created_at`

### `research_input_chain_snapshots`

Purpose:

- DB-first staging table for the option chain state available at each research event

Suggested columns:

- `row_pk`
- `event_id`
- `symbol`
- `asof_ts`
- `option_symbol`
- `right_side`
- `expiration_date`
- `strike`
- `dte`
- `bid`
- `ask`
- `last`
- `delta`
- `gamma`
- `theta`
- `vega`
- `iv`
- `open_interest`
- `volume`
- `source_tag`
- `notes_json`

### `research_input_outcomes`

Purpose:

- DB-first staging table for replaying deterministic option trade outcomes during research validation

Suggested columns:

- `outcome_id`
- `event_id`
- `option_symbol`
- `exit_ts`
- `entry_fill`
- `exit_fill`
- `entry_bid`
- `entry_ask`
- `exit_bid`
- `exit_ask`
- `entry_delta`
- `exit_delta`
- `entry_iv`
- `exit_iv`
- `exit_reason`
- `underlying_entry_px`
- `underlying_exit_px`
- `underlying_peak_px`
- `underlying_trough_px`
- `peak_contract_mid`
- `trough_contract_mid`
- `max_favorable_excursion`
- `max_adverse_excursion`
- `holding_minutes`
- `profit_lock_triggered`
- `fees_estimate`
- `entry_slippage_estimate`
- `exit_slippage_estimate`
- `split_bucket`
- `outcome_json`
- `created_at`

### `research_input_option_bar_paths`

Purpose:

- preserve the event-to-exit intraday option price path for each staged contract
- support exit-variant research such as early take-profit, green-lock, and no-progress exits without reconstructing future state from summary fields

Suggested columns:

- `row_pk`
- `event_id`
- `option_symbol`
- `ts`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `source_tag`
- `notes_json`

### `research_input_underlying_bar_paths`

Purpose:

- preserve the event-forward intraday underlying price path at `1Min`
- support behavior-layer exits such as stall / no-progress / momentum-decay
- keep underlying behavior research DB-first instead of reconstructing it ad hoc during experiments

Suggested columns:

- `row_pk`
- `event_id`
- `symbol`
- `ts`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `timeframe_min`
- `source_tag`
- `notes_json`

### `underlying_bars_intraday`

Purpose:

- store underlying bars used by the options signal engine

Suggested columns:

- `symbol`
- `ts`
- `timeframe_min`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `session_date`
- `provider_ts`
- `ingested_at`
- `bar_status`
- `source_provider`

### `underlying_r6_signals`

Purpose:

- persist underlying signal events that may trigger an options trade

Suggested columns:

- `event_id`
- `symbol`
- `session_date`
- `event_ts`
- `direction`
- `variant_id`
- `ref_horizon`
- `include_today_or`
- `ref_high`
- `ref_low`
- `ref_width`
- `confidence`
- `bar_open`
- `bar_high`
- `bar_low`
- `bar_close`
- `ema20`
- `ema20_slope`
- `signal_phase`
- `underlying_price`
- `underlying_stop_price`
- `signal_age_seconds`
- `eligibility_json`
- `raw_signal_json`
- `data_json`

### `option_chain_snapshots`

Purpose:

- keep the exact chain state seen at decision time

Suggested columns:

- `snapshot_id`
- `symbol`
- `asof_ts`
- `option_symbol`
- `right`
- `expiration_date`
- `strike`
- `dte`
- `bid`
- `ask`
- `bid_size`
- `ask_size`
- `mid`
- `spread_pct`
- `mark_price`
- `last`
- `quote_ts`
- `delta`
- `gamma`
- `theta`
- `vega`
- `iv`
- `in_the_money`
- `intrinsic_value`
- `extrinsic_value`
- `open_interest`
- `volume`
- `underlying_price`
- `snapshot_batch_id`
- `source_payload_json`
- `source_provider`

### `option_chain_batches`

Purpose:

- persist each chain fetch as a batch-level decision context row

Suggested columns:

- `batch_id`
- `run_id`
- `symbol`
- `event_id`
- `session_date`
- `asof_ts`
- `source_provider`
- `underlying_price`
- `contracts_seen`
- `contracts_eligible`
- `notes_json`
- `created_at`

### `option_contract_candidates`

Purpose:

- log all contracts evaluated for a signal and why they passed or failed

Suggested columns:

- `candidate_id`
- `run_id`
- `event_id`
- `symbol`
- `option_symbol`
- `right`
- `expiration_date`
- `strike`
- `dte`
- `delta`
- `bid`
- `ask`
- `mid`
- `spread_pct`
- `open_interest`
- `volume`
- `passed_filters`
- `reject_reason`
- `rank_score`
- `snapshot_id`
- `filter_flags_json`
- `decision_ts`
- `selection_context_json`

### `selected_option_contracts`

Purpose:

- record the single chosen contract for each accepted trade

Suggested columns:

- `selection_id`
- `run_id`
- `event_id`
- `symbol`
- `option_symbol`
- `right`
- `expiration_date`
- `strike`
- `dte`
- `delta`
- `bid`
- `ask`
- `mid`
- `spread_pct`
- `snapshot_id`
- `selection_rank`
- `selection_score`
- `selection_limit_price`
- `selection_reason`
- `selection_context_json`
- `created_at`

### `options_positions`

Purpose:

- track live paper or simulated open positions

Suggested columns:

- `position_id`
- `run_id`
- `symbol`
- `option_symbol`
- `event_id`
- `strategy_id`
- `side`
- `contracts`
- `entry_ts`
- `entry_limit`
- `entry_fill`
- `entry_bid`
- `entry_ask`
- `entry_mid`
- `entry_delta`
- `entry_iv`
- `entry_spread_pct`
- `entry_open_interest`
- `entry_volume`
- `entry_slippage_estimate`
- `premium_at_risk`
- `premium_hard_stop`
- `underlying_entry_px`
- `underlying_stop_px`
- `entry_quote_ts`
- `entry_snapshot_id`
- `selection_id`
- `contract_multiplier`
- `time_exit_ts`
- `max_contract_mid`
- `min_contract_mid`
- `max_underlying_price`
- `min_underlying_price`
- `last_mark_ts`
- `status`
- `broker_order_id`
- `underlying_event_json`
- `risk_context_json`
- `decision_context_json`
- `exit_plan_json`
- `broker_position_json`
- `session_date`
- `created_at`
- `updated_at`

### `options_trades`

Purpose:

- closed-trade ledger

Suggested columns:

- `trade_id`
- `run_id`
- `position_id`
- `symbol`
- `option_symbol`
- `entry_ts`
- `exit_ts`
- `contracts`
- `entry_fill`
- `exit_fill`
- `entry_bid`
- `entry_ask`
- `entry_mid`
- `exit_bid`
- `exit_ask`
- `exit_mid`
- `entry_delta`
- `exit_delta`
- `entry_iv`
- `exit_iv`
- `entry_spread_pct`
- `exit_spread_pct`
- `gross_pnl`
- `net_pnl`
- `return_pct`
- `exit_reason`
- `event_id`
- `selection_id`
- `entry_snapshot_id`
- `exit_snapshot_id`
- `underlying_entry_px`
- `underlying_exit_px`
- `underlying_stop_px`
- `underlying_peak_px`
- `underlying_trough_px`
- `max_favorable_excursion`
- `max_adverse_excursion`
- `peak_contract_mid`
- `trough_contract_mid`
- `peak_unrealized_pnl`
- `trough_unrealized_pnl`
- `holding_minutes`
- `profit_lock_triggered`
- `entry_slippage_estimate`
- `exit_slippage_estimate`
- `fees_estimate`
- `contract_multiplier`
- `exit_signal_json`
- `trade_diagnostics_json`
- `created_at`

### `options_orders`

Purpose:

- persist every broker order lifecycle transition for audit and replay

Suggested columns:

- `order_pk`
- `run_id`
- `order_id`
- `position_id`
- `event_id`
- `symbol`
- `option_symbol`
- `broker_order_id`
- `client_order_id`
- `side`
- `order_type`
- `tif`
- `limit_price`
- `stop_price`
- `qty`
- `parent_order_id`
- `purpose`
- `status`
- `filled_qty`
- `filled_avg_price`
- `submitted_at`
- `updated_at`
- `transition_json`
- `raw_json`

### `options_position_marks`

Purpose:

- store mark-to-market path while a position is open

Suggested columns:

- `mark_pk`
- `position_id`
- `ts`
- `underlying_price`
- `bid`
- `ask`
- `mid`
- `quote_ts`
- `spread_pct`
- `delta`
- `iv`
- `theta`
- `vega`
- `unrealized_pnl`
- `unrealized_return_pct`
- `data_json`

### `options_events`

Purpose:

- warnings, info, errors, state transitions

Suggested columns:

- `event_pk`
- `run_id`
- `ts`
- `event_id`
- `symbol`
- `level`
- `event_type`
- `session_date`
- `stage`
- `message`
- `data_json`

### `options_decision_steps`

Purpose:

- record the structured yes/no flow of the engine, not just free-form logs

Suggested columns:

- `step_pk`
- `run_id`
- `ts`
- `event_id`
- `position_id`
- `trade_id`
- `symbol`
- `option_symbol`
- `stage`
- `decision`
- `reason_code`
- `score`
- `data_json`

### `options_missed_trades`

Purpose:

- log opportunities not taken

Suggested columns:

- `miss_id`
- `run_id`
- `event_id`
- `symbol`
- `stage`
- `reason`
- `selection_context_json`
- `data_json`
- `created_at`

### `options_strategy_runs`

Purpose:

- record research and paper run metadata

Suggested columns:

- `run_id`
- `mode`
- `started_at`
- `completed_at`
- `status`
- `config_json`
- `summary_json`

### `options_daily_metrics`

Purpose:

- portfolio and per-day evaluation

Suggested columns:

- `session_date`
- `run_id`
- `gross_pnl`
- `net_pnl`
- `return_pct`
- `trades`
- `wins`
- `losses`
- `gross_wins`
- `gross_losses`
- `max_drawdown`
- `fees_estimate`
- `slippage_estimate`
- `exposure_json`
- `notes_json`

### `options_daily_metrics_runs`

Purpose:

- run-scoped daily metrics table for research replay and future walk-forward comparisons

Suggested columns:

- `metric_id`
- `run_id`
- `session_date`
- `gross_pnl`
- `net_pnl`
- `return_pct`
- `trades`
- `wins`
- `losses`
- `gross_wins`
- `gross_losses`
- `max_drawdown`
- `fees_estimate`
- `slippage_estimate`
- `exposure_json`
- `notes_json`

### `options_protocol_runs`

Purpose:

- record each split-aware sweep run and whether blind metrics were kept hidden or revealed

Suggested columns:

- `protocol_run_id`
- `label`
- `created_at`
- `start`
- `end`
- `starting_equity`
- `reveal_blind`
- `selection_policy`
- `train_start`
- `train_end`
- `validation_start`
- `validation_end`
- `blind_start`
- `blind_end`
- `configured_provider`
- `total_variants`
- `winning_contract_variant`
- `winning_exit_variant`
- `output_path`
- `summary_json`

### `options_protocol_results`

Purpose:

- persist per-variant split summaries from a protocol sweep so ranking decisions are queryable in SQL

Suggested columns:

- `result_pk`
- `protocol_run_id`
- `rank`
- `contract_variant`
- `exit_variant`
- `selection_score`
- `train_json`
- `validation_json`
- `blind_json`
- `all_json`
- `notes_json`

### `options_portfolio_snapshots`

Purpose:

- capture portfolio state through the session for research, intraday drawdown review, and restart diagnosis

Suggested columns:

- `snap_pk`
- `run_id`
- `ts`
- `session_date`
- `mode`
- `cash`
- `buying_power`
- `portfolio_value`
- `options_market_value`
- `realized_pnl`
- `unrealized_pnl`
- `open_positions`
- `open_orders`
- `exposure_json`
- `broker_account_json`

## Research Views

### `vw_options_trade_research`

Purpose:

- flatten the most common trade-review join across trades, positions, signals, and selected contracts

Typical use:

- trade postmortems
- blind-test performance slicing
- contract-selection review

### `vw_options_order_audit`

Purpose:

- flatten order lifecycle review without manually joining positions and signals

Typical use:

- fill-quality analysis
- operational failure diagnosis
- replay of entry and exit behavior

## Required Audit Properties

For every completed trade we should be able to reconstruct:

- what the underlying signal was
- what contracts were considered
- why the chosen contract won
- what risk budget was used
- how the order was filled
- why the exit happened
- what spread and slippage assumptions applied
- how the contract and underlying were marked while the trade was open
- whether the failure was signal, contract, execution, or operational
