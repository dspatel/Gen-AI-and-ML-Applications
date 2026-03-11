from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str) -> sqlite3.Connection:
    p = Path(db_path)
    if not p.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        p = (project_root / p).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candles (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_ts_utc TEXT NOT NULL,
            close_ts_utc TEXT NOT NULL,
            open_ts_cst TEXT NOT NULL,
            close_ts_cst TEXT NOT NULL,
            cst_date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL,
            source TEXT NOT NULL,
            ingested_at_cst TEXT NOT NULL,
            PRIMARY KEY (symbol, interval, open_ts_utc)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ema20_candles_symbol_interval_date
        ON candles(symbol, interval, cst_date)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ema20_strategy_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            config_path TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            symbols_csv TEXT NOT NULL,
            variant_count INTEGER NOT NULL,
            trades_count INTEGER NOT NULL,
            summary_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ema20_trades (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_ts TEXT NOT NULL,
            exit_ts TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            exit_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            stop_at_entry REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            hold_days INTEGER NOT NULL,
            return_pct REAL NOT NULL,
            weighted_return_pct REAL NOT NULL,
            r_mult REAL NOT NULL,
            size_mult REAL NOT NULL,
            cross_date TEXT,
            cross_lookback_days INTEGER NOT NULL,
            flat_threshold REAL NOT NULL,
            entry_variant TEXT NOT NULL,
            exit_variant TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ema20_trades_run_variant
        ON ema20_trades(run_id, variant_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ema20_metrics (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            trades_count INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_return_pct REAL NOT NULL,
            avg_weighted_return_pct REAL NOT NULL,
            total_return_pct REAL NOT NULL,
            profit_factor REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            avg_hold_days REAL NOT NULL,
            long_trades INTEGER NOT NULL,
            short_trades INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ema20_metrics_run
        ON ema20_metrics(run_id)
        """
    )
    conn.commit()

