from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Tuple, Optional
from zoneinfo import ZoneInfo
import sqlite3
import pandas as pd

CST = ZoneInfo("America/Chicago")

@dataclass(frozen=True)
class OpeningRangeRow:
    symbol: str
    session_date_cst: str
    or_minutes: int
    interval: str

    or_start_ts_cst: str
    or_end_ts_cst: str

    or_open: float
    or_close: float
    or_high: float
    or_low: float
    or_mid: float
    or_range: float
    or_volume: Optional[float]
    or_num_bars: int

    session_open: float
    session_close: float
    session_high: float
    session_low: float
    session_range: float
    session_volume: Optional[float]

    expected_or_bars: int
    missing_or_bars: int
    is_or_complete: int

    expected_session_bars: int
    missing_session_bars: int
    is_session_complete: int

    computed_at_cst: str


def _interval_minutes(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1])
    if interval.endswith("h"):
        return int(interval[:-1]) * 60
    raise ValueError(f"Unsupported interval for minutes conversion: {interval}")


def _expected_bar_count(window_minutes: int, interval_minutes: int) -> int:
    # For bar-open timestamps, expected count is window_minutes / interval_minutes when divisible.
    # If not divisible, we use ceil to define expectation conservatively.
    q, r = divmod(window_minutes, interval_minutes)
    return q if r == 0 else (q + 1)


def ensure_opening_ranges_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opening_ranges (
            symbol TEXT NOT NULL,
            session_date_cst TEXT NOT NULL,
            or_minutes INTEGER NOT NULL,
            interval TEXT NOT NULL,

            or_start_ts_cst TEXT NOT NULL,
            or_end_ts_cst TEXT NOT NULL,

            or_open REAL NOT NULL,
            or_close REAL NOT NULL,
            or_high REAL NOT NULL,
            or_low REAL NOT NULL,
            or_mid REAL NOT NULL,
            or_range REAL NOT NULL,
            or_volume REAL,
            or_num_bars INTEGER NOT NULL,

            session_open REAL NOT NULL,
            session_close REAL NOT NULL,
            session_high REAL NOT NULL,
            session_low REAL NOT NULL,
            session_range REAL NOT NULL,
            session_volume REAL,

            expected_or_bars INTEGER NOT NULL,
            missing_or_bars INTEGER NOT NULL,
            is_or_complete INTEGER NOT NULL,

            expected_session_bars INTEGER NOT NULL,
            missing_session_bars INTEGER NOT NULL,
            is_session_complete INTEGER NOT NULL,

            computed_at_cst TEXT NOT NULL,

            PRIMARY KEY (symbol, session_date_cst, or_minutes)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_opening_ranges_symbol_date
        ON opening_ranges(symbol, session_date_cst);
        """
    )
    conn.commit()


def upsert_opening_ranges(conn: sqlite3.Connection, rows: List[OpeningRangeRow]) -> Tuple[int, int]:
    inserted = 0
    skipped = 0
    sql = """
    INSERT OR REPLACE INTO opening_ranges (
        symbol, session_date_cst, or_minutes, interval,
        or_start_ts_cst, or_end_ts_cst,
        or_open, or_close, or_high, or_low, or_mid, or_range, or_volume, or_num_bars,
        session_open, session_close, session_high, session_low, session_range, session_volume,
        expected_or_bars, missing_or_bars, is_or_complete,
        expected_session_bars, missing_session_bars, is_session_complete,
        computed_at_cst
    ) VALUES (
        :symbol, :session_date_cst, :or_minutes, :interval,
        :or_start_ts_cst, :or_end_ts_cst,
        :or_open, :or_close, :or_high, :or_low, :or_mid, :or_range, :or_volume, :or_num_bars,
        :session_open, :session_close, :session_high, :session_low, :session_range, :session_volume,
        :expected_or_bars, :missing_or_bars, :is_or_complete,
        :expected_session_bars, :missing_session_bars, :is_session_complete,
        :computed_at_cst
    );
    """
    with conn:
        for r in rows:
            conn.execute(sql, r.__dict__)
            inserted += 1
    return inserted, skipped


def compute_opening_ranges(
    conn: sqlite3.Connection,
    symbols: List[str],
    session_dates_cst: List[str],
    interval: str,
    or_minutes: int,
    session_start: str,
    session_end: str,
) -> Tuple[int, int, int]:
    """
    Returns: (rows_computed, rows_upserted, rows_incomplete)
    """
    ensure_opening_ranges_table(conn)

    interval_min = _interval_minutes(interval)

    # Parse session times
    sh, sm = [int(x) for x in session_start.split(":")]
    eh, em = [int(x) for x in session_end.split(":")]

    # OR window: session_start -> session_start + or_minutes
    or_start_min = sh * 60 + sm
    or_end_min = or_start_min + or_minutes

    session_minutes = (eh * 60 + em) - (sh * 60 + sm)
    expected_session_bars = _expected_bar_count(session_minutes, interval_min)
    expected_or_bars = _expected_bar_count(or_minutes, interval_min)

    # Pull required candles from DB
    q = """
    SELECT symbol, interval, cst_date, open_ts_utc, open_ts_cst,
           open, high, low, close, volume
    FROM candles
    WHERE interval = ?
      AND symbol IN ({sym_placeholders})
      AND cst_date IN ({date_placeholders})
    """
    sym_ph = ",".join(["?"] * len(symbols))
    date_ph = ",".join(["?"] * len(session_dates_cst))
    q = q.format(sym_placeholders=sym_ph, date_placeholders=date_ph)

    params = [interval] + symbols + session_dates_cst
    df = pd.read_sql_query(q, conn, params=params)

    if df.empty:
        return 0, 0, 0

    # Ensure ordering
    df = df.sort_values(["symbol", "cst_date", "open_ts_utc"])

    rows: List[OpeningRangeRow] = []
    incomplete = 0
    now_cst = datetime.now(CST).isoformat()

    # Compute minutes-of-day from open_ts_cst string (ISO)
    # open_ts_cst like 2026-02-11T08:30:00-06:00
    open_dt = pd.to_datetime(df["open_ts_cst"])
    mins = open_dt.dt.hour * 60 + open_dt.dt.minute
    df["_mins"] = mins

    for (sym, d), g in df.groupby(["symbol", "cst_date"], sort=False):
        # Session slice
        gs = g[(g["_mins"] >= (sh*60+sm)) & (g["_mins"] <= (eh*60+em))].copy()
        if gs.empty:
            continue

        # OR slice: [start, end)
        gor = g[(g["_mins"] >= or_start_min) & (g["_mins"] < or_end_min)].copy()

        # Session aggregates
        session_open = float(gs.iloc[0]["open"])
        session_close = float(gs.iloc[-1]["close"])
        session_high = float(gs["high"].max())
        session_low = float(gs["low"].min())
        session_range = session_high - session_low
        session_volume = float(gs["volume"].sum()) if gs["volume"].notna().any() else None
        actual_session_bars = int(len(gs))

        # OR aggregates (if missing, mark incomplete)
        actual_or_bars = int(len(gor))
        if actual_or_bars == 0:
            incomplete += 1
            # Still store a row? For now: store an incomplete row with session stats but OR values from session_open
            # However you asked "stores 30 min OR"—we'll skip if OR not present.
            continue

        or_open = float(gor.iloc[0]["open"])
        or_close = float(gor.iloc[-1]["close"])
        or_high = float(gor["high"].max())
        or_low = float(gor["low"].min())
        or_mid = (or_high + or_low) / 2.0
        or_range = or_high - or_low
        or_volume = float(gor["volume"].sum()) if gor["volume"].notna().any() else None

        missing_or = max(0, expected_or_bars - actual_or_bars)
        missing_session = max(0, expected_session_bars - actual_session_bars)

        is_or_complete = 1 if missing_or == 0 else 0
        is_session_complete = 1 if missing_session == 0 else 0

        if not is_or_complete or not is_session_complete:
            incomplete += 1

        # Build timestamps (CST) for start/end using date + configured times
        # Store as ISO-like strings with no seconds ambiguity
        or_start_ts_cst = f"{d}T{session_start}:00"
        # Compute OR end time string
        end_hour = or_end_min // 60
        end_min = or_end_min % 60
        or_end_ts_cst = f"{d}T{end_hour:02d}:{end_min:02d}:00"

        rows.append(OpeningRangeRow(
            symbol=sym,
            session_date_cst=d,
            or_minutes=or_minutes,
            interval=interval,

            or_start_ts_cst=or_start_ts_cst,
            or_end_ts_cst=or_end_ts_cst,

            or_open=or_open,
            or_close=or_close,
            or_high=or_high,
            or_low=or_low,
            or_mid=or_mid,
            or_range=or_range,
            or_volume=or_volume,
            or_num_bars=actual_or_bars,

            session_open=session_open,
            session_close=session_close,
            session_high=session_high,
            session_low=session_low,
            session_range=session_range,
            session_volume=session_volume,

            expected_or_bars=expected_or_bars,
            missing_or_bars=missing_or,
            is_or_complete=is_or_complete,

            expected_session_bars=expected_session_bars,
            missing_session_bars=missing_session,
            is_session_complete=is_session_complete,

            computed_at_cst=now_cst,
        ))

    upserted, _ = upsert_opening_ranges(conn, rows)
    return len(rows), upserted, incomplete
