from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS bars_5m (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    o REAL NOT NULL,
    h REAL NOT NULL,
    l REAL NOT NULL,
    c REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY(symbol, ts)
);

CREATE TABLE IF NOT EXISTS candle_audit_intraday (
    audit_id TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval_min INTEGER NOT NULL,
    session_date TEXT NOT NULL,
    ts TEXT NOT NULL,
    o REAL NOT NULL,
    h REAL NOT NULL,
    l REAL NOT NULL,
    c REAL NOT NULL,
    volume REAL,
    note TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candle_audit_unique
    ON candle_audit_intraday(source_provider, symbol, interval_min, ts);

CREATE INDEX IF NOT EXISTS idx_candle_audit_symbol_session
    ON candle_audit_intraday(symbol, session_date, interval_min);

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe_min INTEGER,
    session_date TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_ts TEXT NOT NULL,
    exit_ts TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL,
    risk REAL NOT NULL,
    r_mult REAL NOT NULL,
    pnl REAL NOT NULL,
    ret_pct REAL,
    trade_limit_1d INTEGER,
    long_cutoff_ct TEXT,
    exit_reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    timeframe_min INTEGER,
    trade_limit_1d INTEGER,
    long_cutoff_ct TEXT,
    trades_count INTEGER NOT NULL,
    win_rate REAL,
    avg_r REAL,
    profit_factor REAL,
    max_drawdown REAL,
    total_return_pct REAL
);

CREATE INDEX IF NOT EXISTS idx_bars_5m_symbol_ts ON bars_5m(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_trades_run_strategy ON trades(run_id, strategy_id);

CREATE TABLE IF NOT EXISTS strategy_selections (
    selection_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    asof_date TEXT NOT NULL,
    frequency TEXT NOT NULL,
    side_mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    lookback_months INTEGER NOT NULL,
    validation_months INTEGER NOT NULL,
    train_start_date TEXT NOT NULL,
    train_end_date TEXT NOT NULL,
    val_start_date TEXT NOT NULL,
    val_end_date TEXT NOT NULL,
    train_trades INTEGER NOT NULL,
    val_trades INTEGER NOT NULL,
    train_return_pct REAL NOT NULL,
    val_return_pct REAL NOT NULL,
    train_pf REAL NOT NULL,
    val_pf REAL NOT NULL,
    train_avg_r REAL NOT NULL,
    val_avg_r REAL NOT NULL,
    rank_score REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_strategy_selections_symbol_asof
    ON strategy_selections(symbol, asof_date);
CREATE INDEX IF NOT EXISTS idx_strategy_selections_active
    ON strategy_selections(is_active, frequency, side_mode, asof_date);

CREATE TABLE IF NOT EXISTS live_positions (
    position_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_ts TEXT NOT NULL,
    entry_price REAL NOT NULL,
    initial_stop_price REAL,
    stop_price REAL NOT NULL,
    risk REAL NOT NULL,
    or_high REAL NOT NULL,
    or_low REAL NOT NULL,
    timeframe_min INTEGER NOT NULL,
    exit_variant TEXT NOT NULL,
    trade_limit_1d INTEGER NOT NULL,
    long_cutoff_ct TEXT NOT NULL,
    progress_hit INTEGER NOT NULL DEFAULT 0,
    be_armed INTEGER NOT NULL DEFAULT 0,
    bars_since_entry INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    broker_order_id TEXT,
    stop_order_id TEXT,
    data_provider TEXT,
    last_bar_ts TEXT,
    exit_ts TEXT,
    exit_price REAL,
    exit_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_positions_symbol_status
    ON live_positions(symbol, status);

CREATE TABLE IF NOT EXISTS live_trades (
    trade_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    entry_ts TEXT NOT NULL,
    exit_ts TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    initial_stop_price REAL NOT NULL,
    final_stop_price REAL,
    risk REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    r_mult REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    timeframe_min INTEGER NOT NULL,
    exit_variant TEXT NOT NULL,
    trade_limit_1d INTEGER NOT NULL,
    long_cutoff_ct TEXT NOT NULL,
    data_provider TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_trades_symbol_entry_ts ON live_trades(symbol, entry_ts);
CREATE INDEX IF NOT EXISTS idx_live_trades_session_date ON live_trades(session_date);

CREATE TABLE IF NOT EXISTS live_events (
    event_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    symbol TEXT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_live_events_created_at ON live_events(created_at);

CREATE TABLE IF NOT EXISTS live_signal_locks (
    signal_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    session_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_ts TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_live_signal_locks_unique
    ON live_signal_locks(session_date, symbol, strategy_id, side, entry_ts);

CREATE TABLE IF NOT EXISTS missed_trades (
    miss_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    agent TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT,
    strategy_id TEXT,
    side TEXT,
    signal_ts TEXT,
    entry_price REAL,
    stop_price REAL,
    risk REAL,
    planned_qty INTEGER,
    reason TEXT NOT NULL,
    data_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_missed_trades_symbol_session ON missed_trades(symbol, session_date);
""" 


@dataclass
class Database:
    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)
            self._ensure_column(conn, "trades", "timeframe_min", "INTEGER")
            self._ensure_column(conn, "trades", "ret_pct", "REAL")
            self._ensure_column(conn, "trades", "trade_limit_1d", "INTEGER")
            self._ensure_column(conn, "trades", "long_cutoff_ct", "TEXT")
            self._ensure_column(conn, "metrics", "timeframe_min", "INTEGER")
            self._ensure_column(conn, "metrics", "trade_limit_1d", "INTEGER")
            self._ensure_column(conn, "metrics", "long_cutoff_ct", "TEXT")
            self._ensure_column(conn, "metrics", "total_return_pct", "REAL")
            self._ensure_column(conn, "strategy_selections", "rank_score", "REAL")
            self._ensure_column(conn, "strategy_selections", "is_active", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "live_positions", "last_bar_ts", "TEXT")
            self._ensure_column(conn, "live_positions", "stop_order_id", "TEXT")
            self._ensure_column(conn, "live_positions", "initial_stop_price", "REAL")
            self._ensure_column(conn, "live_positions", "data_provider", "TEXT")
            self._ensure_column(conn, "live_trades", "data_provider", "TEXT")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def replace_table_rows(self, table: str, where_sql: str, params: tuple, df: pd.DataFrame) -> None:
        with self.connect() as conn:
            conn.execute(f"DELETE FROM {table} WHERE {where_sql}", params)
            if not df.empty:
                df.to_sql(table, conn, if_exists="append", index=False, method="multi", chunksize=500)

    def insert_run(self, run_id: str, started_at: str, mode: str, symbol: str, start_date: str, end_date: str, provider: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_runs
                (run_id, started_at, mode, symbol, start_date, end_date, provider, status, summary_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'running', NULL)
                """,
                (run_id, started_at, mode, symbol, start_date, end_date, provider),
            )

    def complete_run(self, run_id: str, status: str, summary: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE strategy_runs SET status = ?, summary_json = ? WHERE run_id = ?",
                (status, json.dumps(summary, sort_keys=True), run_id),
            )

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self.connect() as conn:
            conn.execute(sql, params)

    def query_df(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(sql, conn, params=params)

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = conn.execute(f"PRAGMA table_info({table})").fetchall()
        names = {row[1] for row in existing}
        if column not in names:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
