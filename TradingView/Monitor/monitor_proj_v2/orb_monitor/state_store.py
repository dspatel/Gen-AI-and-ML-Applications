from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from typing import Optional, Dict, Any
import pandas as pd

from .strategy import SymbolState

def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)

def init_db(db_path: str) -> None:
    _ensure_dir(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_state (
              session_date TEXT NOT NULL,
              symbol TEXT NOT NULL,
              or_ready INTEGER NOT NULL,
              or_high REAL,
              or_low REAL,
              or_notified INTEGER NOT NULL DEFAULT 0,
              armed INTEGER NOT NULL,
              last_confirm_ts TEXT,
              PRIMARY KEY (session_date, symbol)
            );
            """
        )
        # Best-effort migration for older DBs that don't have the new column.
        try:
            con.execute("ALTER TABLE symbol_state ADD COLUMN or_notified INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
        con.commit()

def load_symbol_state(db_path: str, session_date: str, symbol: str) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            """SELECT session_date, symbol, or_ready, or_high, or_low, or_notified, armed, last_confirm_ts
               FROM symbol_state WHERE session_date=? AND symbol=?""",
            (session_date, symbol),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "session_date": row[0],
        "symbol": row[1],
        "or_ready": bool(row[2]),
        "or_high": row[3],
        "or_low": row[4],
        "or_notified": bool(row[5]),
        "armed": bool(row[6]),
        "last_confirm_ts": row[7],
    }

def apply_loaded_state(st: SymbolState, row: Dict[str, Any]) -> None:
    st.or_ready = bool(row.get("or_ready", False))
    st.or_high = float(row["or_high"]) if row.get("or_high") is not None else None
    st.or_low = float(row["or_low"]) if row.get("or_low") is not None else None
    st.or_notified = bool(row.get("or_notified", False))
    st.armed = bool(row.get("armed", True))
    ts = row.get("last_confirm_ts")
    if ts:
        try:
            st.last_confirm_dt_processed = pd.Timestamp(ts)
        except Exception:
            st.last_confirm_dt_processed = None

def save_symbol_state(db_path: str, st: SymbolState) -> None:
    init_db(db_path)
    last_ts = None
    if st.last_confirm_dt_processed is not None:
        # store as ISO string with timezone if present
        last_ts = str(st.last_confirm_dt_processed)
    with sqlite3.connect(db_path) as con:
        con.execute(
            """INSERT INTO symbol_state(session_date, symbol, or_ready, or_high, or_low, or_notified, armed, last_confirm_ts)
                 VALUES(?,?,?,?,?,?,?,?)
                 ON CONFLICT(session_date, symbol) DO UPDATE SET
                   or_ready=excluded.or_ready,
                   or_high=excluded.or_high,
                   or_low=excluded.or_low,
                   or_notified=excluded.or_notified,
                   armed=excluded.armed,
                   last_confirm_ts=excluded.last_confirm_ts
            """,
            (
                st.session_date,
                st.symbol,
                1 if st.or_ready else 0,
                st.or_high,
                st.or_low,
                1 if st.or_notified else 0,
                1 if st.armed else 0,
                last_ts,
            ),
        )
        con.commit()
