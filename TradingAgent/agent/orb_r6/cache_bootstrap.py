from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .db import CandleRow, upsert_candles

CST = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


def bootstrap_candles_from_orb_cache(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    session_dates_cst: list[str],
    session_start: str,
    session_end: str,
    source_db_path: str = "orb_research.db",
) -> dict[str, Any]:
    """Copy historical bars from legacy orb_research.db into ORB_R6 candles.

    Supported intervals:
    - 5m: direct copy from bars_5m
    - 15m: resampled from bars_5m
    """
    if interval not in {"5m", "15m"}:
        return {"available": False, "reason": f"unsupported_interval:{interval}"}
    if not session_dates_cst:
        return {"available": False, "reason": "no_session_dates"}

    src_path = _resolve_source_db_path(source_db_path)
    if not src_path.exists():
        return {"available": False, "reason": f"source_db_missing:{src_path}"}

    start_date = min(session_dates_cst)
    end_date = max(session_dates_cst)
    src_conn = sqlite3.connect(str(src_path))
    try:
        src = pd.read_sql_query(
            """
            SELECT ts, o, h, l, c, volume
            FROM bars_5m
            WHERE symbol=?
              AND substr(ts, 1, 10) >= ?
              AND substr(ts, 1, 10) <= ?
            ORDER BY ts
            """,
            src_conn,
            params=[symbol, start_date, end_date],
        )
    finally:
        src_conn.close()

    if src.empty:
        return {
            "available": True,
            "source_db": str(src_path),
            "source_rows": 0,
            "prepared_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "sessions_with_rows": 0,
        }

    base = _normalize_legacy_5m(
        src=src,
        session_dates_cst=session_dates_cst,
        session_start=session_start,
        session_end=session_end,
    )
    if base.empty:
        return {
            "available": True,
            "source_db": str(src_path),
            "source_rows": int(len(src)),
            "prepared_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "sessions_with_rows": 0,
        }

    if interval == "5m":
        prepared = base.copy()
    else:
        prepared = _resample_to_15m(base=base, session_dates_cst=session_dates_cst, session_start=session_start, session_end=session_end)

    if prepared.empty:
        return {
            "available": True,
            "source_db": str(src_path),
            "source_rows": int(len(src)),
            "prepared_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "sessions_with_rows": 0,
        }

    now_cst = datetime.now(CST).isoformat()
    delta = timedelta(minutes=5 if interval == "5m" else 15)
    source = f"orb_cache:{src_path.name}"
    rows: list[CandleRow] = []
    for r in prepared.itertuples(index=False):
        open_cst = r.open_ts_cst
        close_cst = open_cst + delta
        open_utc = open_cst.tz_convert(UTC)
        close_utc = close_cst.tz_convert(UTC)
        vol = None if pd.isna(r.volume) else float(r.volume)
        rows.append(
            CandleRow(
                symbol=symbol,
                interval=interval,
                open_ts_utc=open_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                close_ts_utc=close_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                open_ts_cst=_iso_with_offset(open_cst),
                close_ts_cst=_iso_with_offset(close_cst),
                cst_date=open_cst.strftime("%Y-%m-%d"),
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=vol,
                source=source,
                ingested_at_cst=now_cst,
            )
        )

    inserted, skipped = upsert_candles(conn, rows)
    return {
        "available": True,
        "source_db": str(src_path),
        "source_rows": int(len(src)),
        "prepared_rows": int(len(prepared)),
        "inserted": int(inserted),
        "skipped": int(skipped),
        "sessions_with_rows": int(prepared["open_ts_cst"].dt.strftime("%Y-%m-%d").nunique()),
    }


def _resolve_source_db_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / p).resolve()


def _normalize_legacy_5m(
    src: pd.DataFrame,
    session_dates_cst: list[str],
    session_start: str,
    session_end: str,
) -> pd.DataFrame:
    ts_utc = pd.to_datetime(src["ts"], errors="coerce", utc=True)
    frame = pd.DataFrame(
        {
            "open_ts_utc": ts_utc,
            "open": pd.to_numeric(src["o"], errors="coerce"),
            "high": pd.to_numeric(src["h"], errors="coerce"),
            "low": pd.to_numeric(src["l"], errors="coerce"),
            "close": pd.to_numeric(src["c"], errors="coerce"),
            "volume": pd.to_numeric(src["volume"], errors="coerce"),
        }
    ).dropna(subset=["open_ts_utc", "open", "high", "low", "close"])
    if frame.empty:
        return frame

    frame["open_ts_cst"] = frame["open_ts_utc"].dt.tz_convert(CST)
    frame["cst_date"] = frame["open_ts_cst"].dt.strftime("%Y-%m-%d")
    frame = frame[frame["cst_date"].isin(set(session_dates_cst))]
    if frame.empty:
        return frame

    open_min, close_min = _session_minutes(session_start, session_end)
    mins = frame["open_ts_cst"].dt.hour * 60 + frame["open_ts_cst"].dt.minute
    frame = frame[(mins >= open_min) & (mins < close_min)].copy()
    frame = frame.sort_values("open_ts_cst").drop_duplicates(subset=["open_ts_cst"], keep="last")
    return frame[["open_ts_cst", "open", "high", "low", "close", "volume"]]


def _resample_to_15m(base: pd.DataFrame, session_dates_cst: list[str], session_start: str, session_end: str) -> pd.DataFrame:
    if base.empty:
        return base
    work = base.set_index("open_ts_cst").sort_index()
    agg = work.resample("15min", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"]).reset_index()
    if agg.empty:
        return agg

    agg["cst_date"] = agg["open_ts_cst"].dt.strftime("%Y-%m-%d")
    agg = agg[agg["cst_date"].isin(set(session_dates_cst))]
    if agg.empty:
        return agg

    open_min, close_min = _session_minutes(session_start, session_end)
    mins = agg["open_ts_cst"].dt.hour * 60 + agg["open_ts_cst"].dt.minute
    agg = agg[(mins >= open_min) & (mins < close_min)].copy()
    return agg[["open_ts_cst", "open", "high", "low", "close", "volume"]]


def _session_minutes(session_start: str, session_end: str) -> tuple[int, int]:
    sh, sm = [int(x) for x in session_start.split(":")]
    eh, em = [int(x) for x in session_end.split(":")]
    return sh * 60 + sm, eh * 60 + em


def _iso_with_offset(ts: pd.Timestamp) -> str:
    s = str(ts.strftime("%Y-%m-%dT%H:%M:%S%z"))
    if len(s) >= 5:
        return f"{s[:-2]}:{s[-2:]}"
    return s
