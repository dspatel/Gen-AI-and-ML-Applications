
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class StoreConfig:
    db_path: str


class SQLiteStore:
    """SQLite store for metrics + events.

    Key design goals:
    - Safe reruns via UPSERT
    - TEST vs PROD separation by db_path
    - Lightweight migrations: add missing columns automatically (ALTER TABLE ADD COLUMN)
    - Robust to extra columns in CSVs by inserting only columns that exist in the table
    """

    def __init__(self, cfg: StoreConfig):
        self.db_path = Path(cfg.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path))
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        return con

    def _table_columns(self, con: sqlite3.Connection, table: str) -> set[str]:
        cur = con.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}  # row[1] is column name

    def _ensure_columns(self, con: sqlite3.Connection, table: str, desired: Dict[str, str]) -> None:
        existing = self._table_columns(con, table)
        for col, coltype in desired.items():
            if col not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_symbol_metrics (
                    asof_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,

                    sessions_requested INTEGER,
                    sessions_nonempty INTEGER,
                    sessions_used INTEGER,
                    sessions_missing_data TEXT,

                    ref_high REAL,
                    ref_low REAL,
                    ref_width REAL,
                    or_overlap_ratio REAL,
                    inflation_factor REAL,

                    median_inside_own_or_pct REAL,
                    median_range_to_or REAL,
                    mean_direction_bias REAL,
                    bias_consistency REAL,

                    interval TEXT,
                    orb_minutes INTEGER,
                    historical_days INTEGER,
                    include_today_or INTEGER,

                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),

                    PRIMARY KEY (asof_date, symbol)
                );
                """
            )

            con.execute(
                """
                CREATE TABLE IF NOT EXISTS breakout_events (
                    asof_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    direction TEXT NOT NULL,

                    ref_high REAL,
                    ref_low REAL,
                    ref_width REAL,

                    close_pen REAL,
                    wick_pen REAL,
                    body_norm REAL,
                    range_norm REAL,

                    -- Reference/behavior features useful for ML (added via migration if missing)
                    or_overlap_ratio REAL,
                    inflation_factor REAL,
                    median_inside_own_or_pct REAL,
                    median_range_to_or REAL,
                    mean_direction_bias REAL,
                    bias_consistency REAL,
                    sessions_used INTEGER,

                    label_open_alignment TEXT,
                    label_reference_shape TEXT,
                    label_regime TEXT,
                    label_direction_bias TEXT,
                    label_breakout_strength TEXT,

                    decision TEXT,
                    confidence REAL,
                    decision_reasons TEXT,

                    message TEXT,

                    created_at TEXT DEFAULT (datetime('now')),

                    PRIMARY KEY (symbol, timestamp, direction)
                );
                """
            )

            # Lightweight migration: ensure new columns exist even if DB was created by older schema
            self._ensure_columns(con, "breakout_events", {
                "or_overlap_ratio": "REAL",
                "inflation_factor": "REAL",
                "median_inside_own_or_pct": "REAL",
                "median_range_to_or": "REAL",
                "mean_direction_bias": "REAL",
                "bias_consistency": "REAL",
                "sessions_used": "INTEGER",
                "decision": "TEXT",
                "confidence": "REAL",
                "decision_reasons": "TEXT",
            })

    @staticmethod
    def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
        """Convert datelike columns to ISO strings when possible."""
        if df is None or df.empty:
            return pd.DataFrame()

        out = df.copy()
        for c in out.columns:
            if c in ("timestamp",):
                # keep as string; pandas may parse automatically, ensure ISO
                out[c] = out[c].astype(str)
            elif c in ("asof_date",):
                out[c] = out[c].astype(str)
        return out

    def upsert_daily_metrics(self, df: pd.DataFrame, run_context: Optional[Dict[str, Any]] = None) -> int:
        """Upsert rows into daily_symbol_metrics.

        - Inserts only columns that exist in the table
        - Adds run_context fields if absent
        """
        df = self._normalize_df(df)
        if df.empty:
            return 0

        if run_context:
            for k, v in {
                "interval": run_context.get("interval"),
                "orb_minutes": run_context.get("orb_minutes"),
                "historical_days": run_context.get("historical_days"),
                "include_today_or": 1 if run_context.get("include_today_or") else 0,
            }.items():
                if k not in df.columns:
                    df[k] = v

        with self._connect() as con:
            cols_in_table = self._table_columns(con, "daily_symbol_metrics")
            cols = [c for c in df.columns if c in cols_in_table]

            if "asof_date" not in cols or "symbol" not in cols:
                raise ValueError("daily metrics df must include asof_date and symbol")

            placeholders = ",".join(["?"] * len(cols))
            col_list = ",".join(cols)

            update_cols = [c for c in cols if c not in ("asof_date", "symbol")]
            update_stmt = ",".join([f"{c}=excluded.{c}" for c in update_cols] + ["updated_at=datetime('now')"])

            sql = f"""
            INSERT INTO daily_symbol_metrics ({col_list})
            VALUES ({placeholders})
            ON CONFLICT(asof_date, symbol) DO UPDATE SET {update_stmt}
            """

            con.executemany(sql, df[cols].itertuples(index=False, name=None))

        return int(len(df))

    def upsert_breakout_events(self, df: pd.DataFrame) -> int:
        """Upsert breakout events.

        Robust to extra columns in events CSVs.
        """
        df = self._normalize_df(df)
        if df.empty:
            return 0

        with self._connect() as con:
            # Ensure migrations applied for this DB file
            self._ensure_columns(con, "breakout_events", {
                "or_overlap_ratio": "REAL",
                "inflation_factor": "REAL",
                "median_inside_own_or_pct": "REAL",
                "median_range_to_or": "REAL",
                "mean_direction_bias": "REAL",
                "bias_consistency": "REAL",
                "sessions_used": "INTEGER",
                "decision": "TEXT",
                "confidence": "REAL",
                "decision_reasons": "TEXT",
            })

            cols_in_table = self._table_columns(con, "breakout_events")
            cols = [c for c in df.columns if c in cols_in_table]

            for req in ("asof_date", "symbol", "timestamp", "direction"):
                if req not in cols:
                    raise ValueError(f"events df must include {req}")

            placeholders = ",".join(["?"] * len(cols))
            col_list = ",".join(cols)

            update_cols = [c for c in cols if c not in ("symbol", "timestamp", "direction")]
            update_stmt = ",".join([f"{c}=excluded.{c}" for c in update_cols])

            sql = f"""
            INSERT INTO breakout_events ({col_list})
            VALUES ({placeholders})
            ON CONFLICT(symbol, timestamp, direction) DO UPDATE SET {update_stmt}
            """

            con.executemany(sql, df[cols].itertuples(index=False, name=None))

        return int(len(df))

    def delete_date_range(self, table: str, start: date, end: date) -> int:
        """Delete rows in [start, end] (inclusive) by asof_date."""
        if table not in ("daily_symbol_metrics", "breakout_events"):
            raise ValueError("unsupported table")
        s = start.isoformat()
        e = end.isoformat()
        with self._connect() as con:
            cur = con.execute(f"DELETE FROM {table} WHERE asof_date >= ? AND asof_date <= ?", (s, e))
            return cur.rowcount

    def read_table(self, table: str, limit: int = 10) -> pd.DataFrame:
        with self._connect() as con:
            return pd.read_sql_query(f"SELECT * FROM {table} LIMIT {int(limit)}", con)
