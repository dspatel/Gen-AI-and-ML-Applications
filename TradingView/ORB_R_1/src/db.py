from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

class DB:
    def _ensure_sql_bindings(self, row: dict, sql: str) -> dict:
        """Ensure sqlite named bindings exist for all :params referenced in sql."""
        r = dict(row)
        for k in re.findall(r':([A-Za-z_][A-Za-z0-9_]*)', sql):
            r.setdefault(k, None)
        return r

    def _ensure_bindings(self, row: dict, keys: list[str]) -> dict:
        """Ensure sqlite named bindings exist for all keys (even if None)."""
        r = dict(row)
        for k in keys:
            r.setdefault(k, None)
        return r

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self.conn.executescript(sql)
        # Lightweight migration: add newly introduced columns if running against an existing DB.
        self._ensure_rr_columns()
        self._ensure_breakout_columns()
        self._ensure_event_horizon_columns()
        self.conn.commit()

    def _ensure_rr_columns(self) -> None:
        """Add missing columns to daily_reference_metrics for forward-compatible upgrades.

        SQLite cannot easily ALTER multiple columns at once, and existing DBs may pre-date
        newer metrics. This keeps the project "zip upgrade" friendly.
        """
        cur = self.conn.execute("PRAGMA table_info(daily_reference_metrics)")
        existing = {row[1] for row in cur.fetchall()}  # row[1] is column name

        # column_name -> SQLite column DDL (after "ADD COLUMN")
        desired = {
            "pairs_total": "INTEGER",
            "or_overlap_pairs_count": "INTEGER",
            "or_overlap_pairs_pct": "REAL",
            "or_overlap_days_count": "INTEGER",
            "or_overlap_days_pct": "REAL",
            "or_overlap_ratio": "REAL",

            "median_inside_own_or_pct": "REAL",
            "median_range_to_or": "REAL",
            "mean_direction_bias": "REAL",
            "bias_consistency": "REAL",
            "median_or_width": "REAL",
            "inflation_factor": "REAL",

            "behavior_days_required": "INTEGER NOT NULL DEFAULT 0",
            "behavior_days_available": "INTEGER NOT NULL DEFAULT 0",
            "behavior_days_missing_json": "TEXT NOT NULL DEFAULT '[]'",
            "behavior_failure_reason": "TEXT NOT NULL DEFAULT ''",
        }

        for col, ddl in desired.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE daily_reference_metrics ADD COLUMN {col} {ddl}")


    def _ensure_breakout_columns(self) -> None:
        cur = self.conn.execute("PRAGMA table_info(breakout_events)")
        existing = {row[1] for row in cur.fetchall()}
        desired = {
            "close_pen": "REAL",
            "wick_pen": "REAL",
            "body_norm": "REAL",
            "range_norm": "REAL",
            "decision": "TEXT",
            "confidence": "REAL",
            "confidence_pct": "INTEGER",
            "labels_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for col, ddl in desired.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE breakout_events ADD COLUMN {col} {ddl}")

    def _ensure_event_horizon_columns(self) -> None:
        cur = self.conn.execute("PRAGMA table_info(event_horizon_metrics)")
        existing = {row[1] for row in cur.fetchall()}
        desired = {
            "close_pen": "REAL",
            "wick_pen": "REAL",
            "body_norm": "REAL",
            "range_norm": "REAL",
            "inflation_factor": "REAL",
        }
        for col, ddl in desired.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE event_horizon_metrics ADD COLUMN {col} {ddl}")
    # ---------- daily_or ----------
    def get_daily_or(self, session_date: str, symbol: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(
            """SELECT * FROM daily_or WHERE session_date=? AND symbol=?""",
            (session_date, symbol),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def upsert_daily_or(self, row: Dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO daily_or(
                    session_date, symbol, or_start, or_end, or_high, or_low, or_width,
                    interval, orb_minutes, source, created_at
                ) VALUES (
                    :session_date, :symbol, :or_start, :or_end, :or_high, :or_low, :or_width,
                    :interval, :orb_minutes, :source, COALESCE(:created_at, datetime('now'))
                )
                ON CONFLICT(session_date, symbol) DO UPDATE SET
                    or_start=excluded.or_start,
                    or_end=excluded.or_end,
                    or_high=excluded.or_high,
                    or_low=excluded.or_low,
                    or_width=excluded.or_width,
                    interval=excluded.interval,
                    orb_minutes=excluded.orb_minutes,
                    source=excluded.source
            """,
            row,
        )
        self.conn.commit()

    # ---------- daily_reference_metrics ----------
    def upsert_rr(self, row: Dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO daily_reference_metrics(
                    asof_date, symbol, horizon_days,
                    ref_high, ref_low, ref_width,

                    pairs_total, or_overlap_pairs_count, or_overlap_pairs_pct,
                    or_overlap_days_count, or_overlap_days_pct, or_overlap_ratio,
                    median_inside_own_or_pct, median_range_to_or, mean_direction_bias, bias_consistency,
                    behavior_days_required, behavior_days_available, behavior_days_missing_json, behavior_failure_reason,

                    required_days, available_days, coverage_ratio, is_valid,
                    missing_or_dates_json, failure_reason,
                    used_today_or, today_or_ready,
                    interval, orb_minutes, include_today_or,
                    created_at
                ) VALUES (
                    :asof_date, :symbol, :horizon_days,
                    :ref_high, :ref_low, :ref_width,

                    :pairs_total, :or_overlap_pairs_count, :or_overlap_pairs_pct,
                    :or_overlap_days_count, :or_overlap_days_pct, :or_overlap_ratio,
                    :median_inside_own_or_pct, :median_range_to_or, :mean_direction_bias, :bias_consistency,
                    :behavior_days_required, :behavior_days_available, :behavior_days_missing_json, :behavior_failure_reason,

                    :required_days, :available_days, :coverage_ratio, :is_valid,
                    :missing_or_dates_json, :failure_reason,
                    :used_today_or, :today_or_ready,
                    :interval, :orb_minutes, :include_today_or,
                    COALESCE(:created_at, datetime('now'))
                )
                ON CONFLICT(asof_date, symbol, horizon_days) DO UPDATE SET
                    ref_high=excluded.ref_high,
                    ref_low=excluded.ref_low,
                    ref_width=excluded.ref_width,

                    pairs_total=excluded.pairs_total,
                    or_overlap_pairs_count=excluded.or_overlap_pairs_count,
                    or_overlap_pairs_pct=excluded.or_overlap_pairs_pct,
                    or_overlap_days_count=excluded.or_overlap_days_count,
                    or_overlap_days_pct=excluded.or_overlap_days_pct,
                    median_inside_own_or_pct=excluded.median_inside_own_or_pct,
                    median_range_to_or=excluded.median_range_to_or,
                    mean_direction_bias=excluded.mean_direction_bias,
                    bias_consistency=excluded.bias_consistency,
                    behavior_days_required=excluded.behavior_days_required,
                    behavior_days_available=excluded.behavior_days_available,
                    behavior_days_missing_json=excluded.behavior_days_missing_json,
                    behavior_failure_reason=excluded.behavior_failure_reason,
                    required_days=excluded.required_days,
                    available_days=excluded.available_days,
                    coverage_ratio=excluded.coverage_ratio,
                    is_valid=excluded.is_valid,
                    missing_or_dates_json=excluded.missing_or_dates_json,
                    failure_reason=excluded.failure_reason,
                    used_today_or=excluded.used_today_or,
                    today_or_ready=excluded.today_or_ready,
                    interval=excluded.interval,
                    orb_minutes=excluded.orb_minutes,
                    include_today_or=excluded.include_today_or
            """,
            row,
        )
        self.conn.commit()



    def get_rr_rows(self, asof_date: str, symbol: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM daily_reference_metrics WHERE asof_date=? AND symbol=? ORDER BY horizon_days ASC",
            (asof_date, symbol),
        )
        return [dict(r) for r in cur.fetchall()]

    def insert_breakout_event(self, row: Dict[str, Any]) -> None:
        # SQLite named bindings require all placeholders to be present.
        row = dict(row)
        row.setdefault("created_at", None)

        sql = """INSERT INTO breakout_events(
                event_id, asof_date, symbol, timestamp, direction, primary_horizon,
                also_horizons_json, close, ref_high, ref_low, ref_width,
                breakout_amt, breakout_strength, message, is_replay, created_at
            ) VALUES (
                :event_id, :asof_date, :symbol, :timestamp, :direction, :primary_horizon,
                :also_horizons_json, :close, :ref_high, :ref_low, :ref_width,
                :breakout_amt, :breakout_strength, :message, :is_replay, COALESCE(:created_at, datetime('now'))
            )"""

        row = self._ensure_sql_bindings(row, sql)
        self.conn.execute(sql, row)
        self.conn.commit()

    def upsert_event_horizon_metrics(self, row: Dict[str, Any]) -> None:
        # SQLite named bindings require all placeholders to be present.
        row = dict(row)
        row.setdefault("created_at", None)

        sql = """INSERT INTO event_horizon_metrics(
                event_id, horizon_days, did_break, break_rank,
                ref_high, ref_low, ref_width,
                breakout_amt, breakout_strength,
                close_pen, wick_pen, body_norm, range_norm,
                or_overlap_pairs_pct, median_inside_own_or_pct, median_range_to_or, mean_direction_bias, bias_consistency, inflation_factor,
                created_at
            ) VALUES (
                :event_id, :horizon_days, :did_break, :break_rank,
                :ref_high, :ref_low, :ref_width,
                :breakout_amt, :breakout_strength,
                :close_pen, :wick_pen, :body_norm, :range_norm,
                :or_overlap_pairs_pct, :median_inside_own_or_pct, :median_range_to_or, :mean_direction_bias, :bias_consistency, :inflation_factor,
                COALESCE(:created_at, datetime('now'))
            )
            ON CONFLICT(event_id, horizon_days) DO UPDATE SET
                did_break=excluded.did_break,
                break_rank=excluded.break_rank,
                ref_high=excluded.ref_high,
                ref_low=excluded.ref_low,
                ref_width=excluded.ref_width,
                breakout_amt=excluded.breakout_amt,
                breakout_strength=excluded.breakout_strength,
                close_pen=excluded.close_pen,
                wick_pen=excluded.wick_pen,
                body_norm=excluded.body_norm,
                range_norm=excluded.range_norm,
                or_overlap_pairs_pct=excluded.or_overlap_pairs_pct,
                median_inside_own_or_pct=excluded.median_inside_own_or_pct,
                median_range_to_or=excluded.median_range_to_or,
                mean_direction_bias=excluded.mean_direction_bias,
                bias_consistency=excluded.bias_consistency,
                inflation_factor=excluded.inflation_factor
            """

        row = self._ensure_sql_bindings(row, sql)
        self.conn.execute(sql, row)
        self.conn.commit()
