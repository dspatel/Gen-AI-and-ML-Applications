from __future__ import annotations

import sqlite3

from .paths import resolve_workspace_path


def connect(db_path: str) -> sqlite3.Connection:
    path = resolve_workspace_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_input_signals (
            event_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            event_ts TEXT NOT NULL,
            direction TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            confidence REAL,
            ref_horizon INTEGER,
            include_today_or INTEGER,
            underlying_price REAL,
            underlying_stop_price REAL,
            bar_open REAL,
            bar_high REAL,
            bar_low REAL,
            bar_close REAL,
            ema20 REAL,
            ema20_slope REAL,
            source_tag TEXT,
            notes_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_input_chain_snapshots (
            row_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asof_ts TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            right_side TEXT NOT NULL,
            expiration_date TEXT NOT NULL,
            strike REAL NOT NULL,
            dte INTEGER NOT NULL,
            bid REAL,
            ask REAL,
            last REAL,
            delta REAL,
            gamma REAL,
            theta REAL,
            vega REAL,
            iv REAL,
            open_interest INTEGER,
            volume INTEGER,
            source_tag TEXT,
            notes_json TEXT
        );

        CREATE TABLE IF NOT EXISTS research_input_outcomes (
            outcome_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            exit_ts TEXT NOT NULL,
            entry_fill REAL NOT NULL,
            exit_fill REAL NOT NULL,
            entry_bid REAL,
            entry_ask REAL,
            exit_bid REAL,
            exit_ask REAL,
            entry_delta REAL,
            exit_delta REAL,
            entry_iv REAL,
            exit_iv REAL,
            exit_reason TEXT NOT NULL,
            underlying_entry_px REAL,
            underlying_exit_px REAL,
            underlying_peak_px REAL,
            underlying_trough_px REAL,
            peak_contract_mid REAL,
            trough_contract_mid REAL,
            max_favorable_excursion REAL,
            max_adverse_excursion REAL,
            holding_minutes REAL,
            profit_lock_triggered INTEGER,
            fees_estimate REAL,
            entry_slippage_estimate REAL,
            exit_slippage_estimate REAL,
            split_bucket TEXT,
            outcome_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_input_option_bar_paths (
            row_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            source_tag TEXT,
            notes_json TEXT
        );

        CREATE TABLE IF NOT EXISTS research_input_underlying_bar_paths (
            row_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            timeframe_min INTEGER NOT NULL,
            source_tag TEXT,
            notes_json TEXT
        );

        CREATE TABLE IF NOT EXISTS underlying_bars_intraday (
            symbol TEXT NOT NULL,
            ts TEXT NOT NULL,
            timeframe_min INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL,
            source_provider TEXT NOT NULL,
            PRIMARY KEY (symbol, ts, timeframe_min)
        );

        CREATE TABLE IF NOT EXISTS underlying_r6_signals (
            event_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            event_ts TEXT NOT NULL,
            direction TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            ref_horizon INTEGER,
            include_today_or INTEGER,
            ref_high REAL,
            ref_low REAL,
            ref_width REAL,
            confidence REAL,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            asof_ts TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            right_side TEXT NOT NULL,
            expiration_date TEXT NOT NULL,
            strike REAL NOT NULL,
            dte INTEGER NOT NULL,
            bid REAL,
            ask REAL,
            mid REAL,
            last REAL,
            delta REAL,
            gamma REAL,
            theta REAL,
            vega REAL,
            iv REAL,
            open_interest INTEGER,
            volume INTEGER,
            source_provider TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS option_chain_batches (
            batch_id TEXT PRIMARY KEY,
            run_id TEXT,
            symbol TEXT NOT NULL,
            event_id TEXT,
            session_date TEXT,
            asof_ts TEXT NOT NULL,
            source_provider TEXT NOT NULL,
            underlying_price REAL,
            contracts_seen INTEGER,
            contracts_eligible INTEGER,
            notes_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS option_contract_candidates (
            candidate_id TEXT PRIMARY KEY,
            run_id TEXT,
            event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            right_side TEXT NOT NULL,
            expiration_date TEXT NOT NULL,
            strike REAL NOT NULL,
            dte INTEGER NOT NULL,
            delta REAL,
            bid REAL,
            ask REAL,
            mid REAL,
            spread_pct REAL,
            open_interest INTEGER,
            volume INTEGER,
            passed_filters INTEGER NOT NULL,
            reject_reason TEXT,
            rank_score TEXT
        );

        CREATE TABLE IF NOT EXISTS selected_option_contracts (
            selection_id TEXT PRIMARY KEY,
            run_id TEXT,
            event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            right_side TEXT NOT NULL,
            expiration_date TEXT NOT NULL,
            strike REAL NOT NULL,
            dte INTEGER NOT NULL,
            delta REAL,
            bid REAL,
            ask REAL,
            mid REAL,
            spread_pct REAL,
            selection_reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS options_positions (
            position_id TEXT PRIMARY KEY,
            run_id TEXT,
            symbol TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            event_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            side TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            entry_ts TEXT NOT NULL,
            entry_limit REAL,
            entry_fill REAL,
            premium_at_risk REAL NOT NULL,
            underlying_entry_px REAL,
            underlying_stop_px REAL,
            status TEXT NOT NULL,
            broker_order_id TEXT,
            session_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS options_trades (
            trade_id TEXT PRIMARY KEY,
            run_id TEXT,
            position_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            entry_ts TEXT NOT NULL,
            exit_ts TEXT NOT NULL,
            contracts INTEGER NOT NULL,
            entry_fill REAL NOT NULL,
            exit_fill REAL NOT NULL,
            gross_pnl REAL NOT NULL,
            net_pnl REAL NOT NULL,
            return_pct REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            underlying_exit_px REAL,
            slippage_estimate REAL,
            fees_estimate REAL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS options_events (
            event_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            ts TEXT NOT NULL,
            event_id TEXT,
            symbol TEXT,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_decision_steps (
            step_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            ts TEXT NOT NULL,
            event_id TEXT,
            position_id TEXT,
            trade_id TEXT,
            symbol TEXT,
            option_symbol TEXT,
            stage TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason_code TEXT,
            score REAL,
            data_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_missed_trades (
            miss_id TEXT PRIMARY KEY,
            run_id TEXT,
            event_id TEXT,
            symbol TEXT NOT NULL,
            reason TEXT NOT NULL,
            data_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS options_strategy_runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            config_json TEXT,
            summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_daily_metrics (
            session_date TEXT PRIMARY KEY,
            run_id TEXT,
            gross_pnl REAL NOT NULL,
            net_pnl REAL NOT NULL,
            return_pct REAL NOT NULL,
            trades INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            max_drawdown REAL,
            notes_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_daily_metrics_runs (
            metric_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            session_date TEXT NOT NULL,
            gross_pnl REAL NOT NULL,
            net_pnl REAL NOT NULL,
            return_pct REAL NOT NULL,
            trades INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            gross_wins REAL,
            gross_losses REAL,
            max_drawdown REAL,
            fees_estimate REAL,
            slippage_estimate REAL,
            exposure_json TEXT,
            notes_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_protocol_runs (
            protocol_run_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            start TEXT,
            end TEXT,
            starting_equity REAL,
            reveal_blind INTEGER NOT NULL,
            selection_policy TEXT,
            train_start TEXT,
            train_end TEXT,
            validation_start TEXT,
            validation_end TEXT,
            blind_start TEXT,
            blind_end TEXT,
            configured_provider TEXT,
            total_variants INTEGER NOT NULL,
            winning_contract_variant TEXT,
            winning_exit_variant TEXT,
            output_path TEXT,
            summary_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_protocol_results (
            result_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            protocol_run_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            contract_variant TEXT NOT NULL,
            exit_variant TEXT NOT NULL,
            selection_score REAL,
            train_json TEXT,
            validation_json TEXT,
            blind_json TEXT,
            all_json TEXT,
            notes_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_portfolio_snapshots (
            snap_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            ts TEXT NOT NULL,
            session_date TEXT,
            mode TEXT NOT NULL,
            cash REAL,
            buying_power REAL,
            portfolio_value REAL,
            options_market_value REAL,
            realized_pnl REAL,
            unrealized_pnl REAL,
            open_positions INTEGER,
            open_orders INTEGER,
            exposure_json TEXT,
            broker_account_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_orders (
            order_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            order_id TEXT,
            position_id TEXT,
            event_id TEXT,
            symbol TEXT,
            option_symbol TEXT,
            broker_order_id TEXT,
            client_order_id TEXT,
            side TEXT,
            order_type TEXT,
            tif TEXT,
            limit_price REAL,
            stop_price REAL,
            qty INTEGER,
            status TEXT,
            filled_qty INTEGER,
            filled_avg_price REAL,
            submitted_at TEXT,
            updated_at TEXT,
            raw_json TEXT
        );

        CREATE TABLE IF NOT EXISTS options_position_marks (
            mark_pk INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            underlying_price REAL,
            bid REAL,
            ask REAL,
            mid REAL,
            delta REAL,
            iv REAL,
            unrealized_pnl REAL,
            unrealized_return_pct REAL,
            data_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_underlying_bars_symbol_ts
        ON underlying_bars_intraday(symbol, ts);

        CREATE INDEX IF NOT EXISTS idx_research_input_signals_session
        ON research_input_signals(session_date, event_ts);

        CREATE INDEX IF NOT EXISTS idx_research_input_chain_event
        ON research_input_chain_snapshots(event_id, option_symbol);

        CREATE INDEX IF NOT EXISTS idx_research_input_outcomes_event
        ON research_input_outcomes(event_id, option_symbol);

        CREATE INDEX IF NOT EXISTS idx_research_input_option_bar_paths_event
        ON research_input_option_bar_paths(event_id, option_symbol, ts);

        CREATE INDEX IF NOT EXISTS idx_research_input_underlying_bar_paths_event
        ON research_input_underlying_bar_paths(event_id, symbol, ts);

        CREATE INDEX IF NOT EXISTS idx_underlying_signals_symbol_session
        ON underlying_r6_signals(symbol, session_date);

        CREATE INDEX IF NOT EXISTS idx_option_chain_symbol_asof
        ON option_chain_snapshots(symbol, asof_ts);

        CREATE INDEX IF NOT EXISTS idx_option_chain_batches_symbol_asof
        ON option_chain_batches(symbol, asof_ts);

        CREATE INDEX IF NOT EXISTS idx_option_candidates_event
        ON option_contract_candidates(event_id);

        CREATE INDEX IF NOT EXISTS idx_options_positions_symbol_status
        ON options_positions(symbol, status);

        CREATE INDEX IF NOT EXISTS idx_options_trades_symbol_exit
        ON options_trades(symbol, exit_ts);

        CREATE INDEX IF NOT EXISTS idx_options_orders_position_id
        ON options_orders(position_id);

        CREATE INDEX IF NOT EXISTS idx_options_orders_broker_order_id
        ON options_orders(broker_order_id);

        CREATE INDEX IF NOT EXISTS idx_options_position_marks_position_ts
        ON options_position_marks(position_id, ts);

        CREATE INDEX IF NOT EXISTS idx_options_decision_steps_event_stage
        ON options_decision_steps(event_id, stage);

        CREATE INDEX IF NOT EXISTS idx_options_portfolio_snapshots_session_ts
        ON options_portfolio_snapshots(session_date, ts);

        CREATE INDEX IF NOT EXISTS idx_options_daily_metrics_runs_run_date
        ON options_daily_metrics_runs(run_id, session_date);

        CREATE INDEX IF NOT EXISTS idx_options_protocol_results_run_rank
        ON options_protocol_results(protocol_run_id, rank);

        """
    )

    _ensure_column(conn, "underlying_bars_intraday", "session_date", "TEXT")
    _ensure_column(conn, "underlying_bars_intraday", "provider_ts", "TEXT")
    _ensure_column(conn, "underlying_bars_intraday", "ingested_at", "TEXT")
    _ensure_column(conn, "underlying_bars_intraday", "bar_status", "TEXT")

    _ensure_column(conn, "underlying_r6_signals", "bar_open", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "bar_high", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "bar_low", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "bar_close", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "ema20", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "ema20_slope", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "signal_phase", "TEXT")
    _ensure_column(conn, "underlying_r6_signals", "raw_signal_json", "TEXT")
    _ensure_column(conn, "underlying_r6_signals", "underlying_price", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "underlying_stop_price", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "signal_age_seconds", "REAL")
    _ensure_column(conn, "underlying_r6_signals", "eligibility_json", "TEXT")

    _ensure_column(conn, "option_chain_snapshots", "snapshot_batch_id", "TEXT")
    _ensure_column(conn, "option_chain_snapshots", "underlying_price", "REAL")
    _ensure_column(conn, "option_chain_snapshots", "spread_pct", "REAL")
    _ensure_column(conn, "option_chain_snapshots", "mark_price", "REAL")
    _ensure_column(conn, "option_chain_snapshots", "source_payload_json", "TEXT")
    _ensure_column(conn, "option_chain_snapshots", "quote_ts", "TEXT")
    _ensure_column(conn, "option_chain_snapshots", "bid_size", "INTEGER")
    _ensure_column(conn, "option_chain_snapshots", "ask_size", "INTEGER")
    _ensure_column(conn, "option_chain_snapshots", "in_the_money", "INTEGER")
    _ensure_column(conn, "option_chain_snapshots", "intrinsic_value", "REAL")
    _ensure_column(conn, "option_chain_snapshots", "extrinsic_value", "REAL")

    _ensure_column(conn, "option_chain_batches", "run_id", "TEXT")

    _ensure_column(conn, "option_contract_candidates", "run_id", "TEXT")
    _ensure_column(conn, "option_contract_candidates", "snapshot_id", "TEXT")
    _ensure_column(conn, "option_contract_candidates", "selection_context_json", "TEXT")
    _ensure_column(conn, "option_contract_candidates", "filter_flags_json", "TEXT")
    _ensure_column(conn, "option_contract_candidates", "decision_ts", "TEXT")

    _ensure_column(conn, "selected_option_contracts", "run_id", "TEXT")
    _ensure_column(conn, "selected_option_contracts", "snapshot_id", "TEXT")
    _ensure_column(conn, "selected_option_contracts", "selection_rank", "INTEGER")
    _ensure_column(conn, "selected_option_contracts", "selection_context_json", "TEXT")
    _ensure_column(conn, "selected_option_contracts", "selection_score", "REAL")
    _ensure_column(conn, "selected_option_contracts", "selection_limit_price", "REAL")

    _ensure_column(conn, "options_positions", "run_id", "TEXT")
    _ensure_column(conn, "options_positions", "selection_id", "TEXT")
    _ensure_column(conn, "options_positions", "entry_snapshot_id", "TEXT")
    _ensure_column(conn, "options_positions", "entry_bid", "REAL")
    _ensure_column(conn, "options_positions", "entry_ask", "REAL")
    _ensure_column(conn, "options_positions", "entry_mid", "REAL")
    _ensure_column(conn, "options_positions", "entry_delta", "REAL")
    _ensure_column(conn, "options_positions", "entry_iv", "REAL")
    _ensure_column(conn, "options_positions", "entry_spread_pct", "REAL")
    _ensure_column(conn, "options_positions", "entry_open_interest", "INTEGER")
    _ensure_column(conn, "options_positions", "entry_volume", "INTEGER")
    _ensure_column(conn, "options_positions", "entry_slippage_estimate", "REAL")
    _ensure_column(conn, "options_positions", "contract_multiplier", "INTEGER NOT NULL DEFAULT 100")
    _ensure_column(conn, "options_positions", "underlying_event_json", "TEXT")
    _ensure_column(conn, "options_positions", "risk_context_json", "TEXT")
    _ensure_column(conn, "options_positions", "decision_context_json", "TEXT")
    _ensure_column(conn, "options_positions", "broker_position_json", "TEXT")
    _ensure_column(conn, "options_positions", "entry_quote_ts", "TEXT")
    _ensure_column(conn, "options_positions", "premium_hard_stop", "REAL")
    _ensure_column(conn, "options_positions", "time_exit_ts", "TEXT")
    _ensure_column(conn, "options_positions", "max_contract_mid", "REAL")
    _ensure_column(conn, "options_positions", "min_contract_mid", "REAL")
    _ensure_column(conn, "options_positions", "max_underlying_price", "REAL")
    _ensure_column(conn, "options_positions", "min_underlying_price", "REAL")
    _ensure_column(conn, "options_positions", "last_mark_ts", "TEXT")
    _ensure_column(conn, "options_positions", "exit_plan_json", "TEXT")

    _ensure_column(conn, "options_trades", "run_id", "TEXT")
    _ensure_column(conn, "options_trades", "event_id", "TEXT")
    _ensure_column(conn, "options_trades", "selection_id", "TEXT")
    _ensure_column(conn, "options_trades", "entry_snapshot_id", "TEXT")
    _ensure_column(conn, "options_trades", "exit_snapshot_id", "TEXT")
    _ensure_column(conn, "options_trades", "entry_bid", "REAL")
    _ensure_column(conn, "options_trades", "entry_ask", "REAL")
    _ensure_column(conn, "options_trades", "entry_mid", "REAL")
    _ensure_column(conn, "options_trades", "exit_bid", "REAL")
    _ensure_column(conn, "options_trades", "exit_ask", "REAL")
    _ensure_column(conn, "options_trades", "exit_mid", "REAL")
    _ensure_column(conn, "options_trades", "entry_delta", "REAL")
    _ensure_column(conn, "options_trades", "exit_delta", "REAL")
    _ensure_column(conn, "options_trades", "entry_iv", "REAL")
    _ensure_column(conn, "options_trades", "exit_iv", "REAL")
    _ensure_column(conn, "options_trades", "entry_spread_pct", "REAL")
    _ensure_column(conn, "options_trades", "exit_spread_pct", "REAL")
    _ensure_column(conn, "options_trades", "underlying_entry_px", "REAL")
    _ensure_column(conn, "options_trades", "underlying_stop_px", "REAL")
    _ensure_column(conn, "options_trades", "underlying_peak_px", "REAL")
    _ensure_column(conn, "options_trades", "underlying_trough_px", "REAL")
    _ensure_column(conn, "options_trades", "max_favorable_excursion", "REAL")
    _ensure_column(conn, "options_trades", "max_adverse_excursion", "REAL")
    _ensure_column(conn, "options_trades", "holding_minutes", "REAL")
    _ensure_column(conn, "options_trades", "entry_slippage_estimate", "REAL")
    _ensure_column(conn, "options_trades", "exit_slippage_estimate", "REAL")
    _ensure_column(conn, "options_trades", "contract_multiplier", "INTEGER NOT NULL DEFAULT 100")
    _ensure_column(conn, "options_trades", "trade_diagnostics_json", "TEXT")
    _ensure_column(conn, "options_trades", "peak_contract_mid", "REAL")
    _ensure_column(conn, "options_trades", "trough_contract_mid", "REAL")
    _ensure_column(conn, "options_trades", "peak_unrealized_pnl", "REAL")
    _ensure_column(conn, "options_trades", "trough_unrealized_pnl", "REAL")
    _ensure_column(conn, "options_trades", "profit_lock_triggered", "INTEGER")
    _ensure_column(conn, "options_trades", "exit_signal_json", "TEXT")

    _ensure_column(conn, "options_events", "run_id", "TEXT")
    _ensure_column(conn, "options_events", "position_id", "TEXT")
    _ensure_column(conn, "options_events", "trade_id", "TEXT")
    _ensure_column(conn, "options_events", "broker_order_id", "TEXT")
    _ensure_column(conn, "options_events", "event_id", "TEXT")
    _ensure_column(conn, "options_events", "session_date", "TEXT")
    _ensure_column(conn, "options_events", "stage", "TEXT")

    _ensure_column(conn, "options_missed_trades", "run_id", "TEXT")
    _ensure_column(conn, "options_missed_trades", "stage", "TEXT")
    _ensure_column(conn, "options_missed_trades", "selection_context_json", "TEXT")

    _ensure_column(conn, "options_daily_metrics", "run_id", "TEXT")
    _ensure_column(conn, "options_daily_metrics", "gross_wins", "REAL")
    _ensure_column(conn, "options_daily_metrics", "gross_losses", "REAL")
    _ensure_column(conn, "options_daily_metrics", "fees_estimate", "REAL")
    _ensure_column(conn, "options_daily_metrics", "slippage_estimate", "REAL")
    _ensure_column(conn, "options_daily_metrics", "exposure_json", "TEXT")

    _ensure_column(conn, "options_orders", "run_id", "TEXT")
    _ensure_column(conn, "options_orders", "parent_order_id", "TEXT")
    _ensure_column(conn, "options_orders", "purpose", "TEXT")
    _ensure_column(conn, "options_orders", "transition_json", "TEXT")

    _ensure_column(conn, "options_position_marks", "run_id", "TEXT")
    _ensure_column(conn, "options_position_marks", "quote_ts", "TEXT")
    _ensure_column(conn, "options_position_marks", "spread_pct", "REAL")
    _ensure_column(conn, "options_position_marks", "theta", "REAL")
    _ensure_column(conn, "options_position_marks", "vega", "REAL")

    _ensure_column(conn, "options_decision_steps", "run_id", "TEXT")
    _ensure_column(conn, "options_portfolio_snapshots", "run_id", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "selection_policy", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "train_start", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "train_end", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "validation_start", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "validation_end", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "blind_start", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "blind_end", "TEXT")
    _ensure_column(conn, "options_protocol_runs", "configured_provider", "TEXT")
    _ensure_column(conn, "options_protocol_results", "selection_score", "REAL")
    _ensure_column(conn, "options_protocol_results", "train_json", "TEXT")
    _ensure_column(conn, "options_protocol_results", "validation_json", "TEXT")
    _ensure_column(conn, "options_protocol_results", "blind_json", "TEXT")
    _ensure_column(conn, "options_protocol_results", "all_json", "TEXT")
    _ensure_column(conn, "options_protocol_results", "notes_json", "TEXT")

    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_option_chain_batches_run_id
        ON option_chain_batches(run_id);

        CREATE INDEX IF NOT EXISTS idx_option_candidates_run_id
        ON option_contract_candidates(run_id);

        CREATE INDEX IF NOT EXISTS idx_options_positions_run_id
        ON options_positions(run_id);

        CREATE INDEX IF NOT EXISTS idx_options_trades_run_id
        ON options_trades(run_id);

        CREATE INDEX IF NOT EXISTS idx_options_orders_run_id
        ON options_orders(run_id);

        CREATE INDEX IF NOT EXISTS idx_options_decision_steps_run_id
        ON options_decision_steps(run_id);

        CREATE INDEX IF NOT EXISTS idx_options_portfolio_snapshots_run_id
        ON options_portfolio_snapshots(run_id);

        CREATE INDEX IF NOT EXISTS idx_options_protocol_runs_label
        ON options_protocol_runs(label);
        """
    )

    conn.executescript(
        """
        DROP VIEW IF EXISTS vw_options_trade_research;
        CREATE VIEW vw_options_trade_research AS
        SELECT
            t.trade_id,
            t.run_id,
            t.position_id,
            t.event_id,
            p.strategy_id,
            t.symbol,
            t.option_symbol,
            s.direction,
            s.variant_id,
            s.session_date,
            s.event_ts,
            s.ref_horizon,
            s.ref_high,
            s.ref_low,
            s.ref_width,
            s.confidence,
            s.bar_open,
            s.bar_high,
            s.bar_low,
            s.bar_close,
            s.ema20,
            s.ema20_slope,
            p.contracts,
            p.premium_at_risk,
            p.entry_limit,
            t.entry_fill,
            t.exit_fill,
            t.entry_bid,
            t.entry_ask,
            t.entry_mid,
            t.exit_bid,
            t.exit_ask,
            t.exit_mid,
            t.entry_delta,
            t.exit_delta,
            t.entry_iv,
            t.exit_iv,
            t.entry_spread_pct,
            t.exit_spread_pct,
            t.underlying_entry_px,
            t.underlying_exit_px,
            t.underlying_stop_px,
            t.underlying_peak_px,
            t.underlying_trough_px,
            t.max_favorable_excursion,
            t.max_adverse_excursion,
            t.holding_minutes,
            t.gross_pnl,
            t.net_pnl,
            t.return_pct,
            t.exit_reason,
            t.entry_slippage_estimate,
            t.exit_slippage_estimate,
            t.fees_estimate,
            sc.expiration_date,
            sc.strike,
            sc.dte,
            sc.selection_reason,
            sc.selection_rank,
            sc.selection_context_json,
            p.risk_context_json,
            p.decision_context_json,
            t.trade_diagnostics_json
        FROM options_trades t
        LEFT JOIN options_positions p ON p.position_id = t.position_id
        LEFT JOIN underlying_r6_signals s ON s.event_id = t.event_id
        LEFT JOIN selected_option_contracts sc ON sc.selection_id = t.selection_id;

        DROP VIEW IF EXISTS vw_options_order_audit;
        CREATE VIEW vw_options_order_audit AS
        SELECT
            o.order_pk,
            o.run_id,
            o.order_id,
            o.position_id,
            o.event_id,
            o.symbol,
            o.option_symbol,
            o.client_order_id,
            o.side,
            o.order_type,
            o.tif,
            o.limit_price,
            o.stop_price,
            o.qty,
            o.status,
            o.filled_qty,
            o.filled_avg_price,
            o.submitted_at,
            o.updated_at,
            p.strategy_id,
            p.status AS position_status,
            s.direction,
            s.variant_id
        FROM options_orders o
        LEFT JOIN options_positions p ON p.position_id = o.position_id
        LEFT JOIN underlying_r6_signals s ON s.event_id = o.event_id;
        """
    )

    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES('schema_version', '0.1.5') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    conn.commit()
