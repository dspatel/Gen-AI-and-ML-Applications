"""DB-first opening-range (OR) resolution.

We want to reuse previously-computed daily opening ranges whenever possible.

Contract:
 - For a (symbol, session_date, interval, orb_minutes) key, if a row exists in DB,
   use it.
 - Otherwise, fetch intraday data for that session, compute the OR, store it,
   and return it.
 - If data is missing (provider returns empty / incomplete), return None and let
   callers decide how to handle it.

This module is intentionally small and side-effect free (except DB upserts).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional

import pandas as pd

from .data_fetch import FetchSpec, fetch_session_bars
from .ranges_or import compute_daily_or
from .sessions import TradingSessions


@dataclass(frozen=True)
class ORKey:
    session_date: date
    symbol: str
    interval: str
    orb_minutes: int


def resolve_daily_or(
    *,
    key: ORKey,
    ts: TradingSessions,
    spec: FetchSpec,
    store,
    provider=None,
) -> Optional[Dict]:
    """Resolve a single session's OR row (DB-first, otherwise fetch+store)."""

    sess_s = key.session_date.isoformat()
    row = store.get_daily_or(
        session_date=sess_s,
        symbol=key.symbol,
        interval=key.interval,
        orb_minutes=key.orb_minutes,
    )
    if row:
        return row

    # Fetch full session bars (cache may be enabled inside fetch_session_bars if configured)
    df = fetch_session_bars(spec, key.session_date, provider=provider)
    if df is None or df.empty:
        return None

    or_start, or_end = ts.get_or_window_bounds(key.session_date, key.orb_minutes)
    or_row = compute_daily_or(df, or_start, or_end)
    if not or_row:
        return None

    # Add key columns expected by the DB schema.
    db_row = {
        "session_date": sess_s,
        "symbol": key.symbol,
        "interval": key.interval,
        "orb_minutes": int(key.orb_minutes),
        **or_row,
    }
    store.upsert_daily_or(pd.DataFrame([db_row]))
    return db_row


def resolve_daily_ors(
    *,
    session_dates: Iterable[date],
    symbol: str,
    interval: str,
    orb_minutes: int,
    ts: TradingSessions,
    spec: FetchSpec,
    store,
    provider=None,
) -> List[Optional[Dict]]:
    """Resolve many OR rows, preserving input order."""

    keys = [
        ORKey(session_date=d, symbol=symbol, interval=interval, orb_minutes=orb_minutes)
        for d in session_dates
    ]

    # Bulk-read what we can
    by_date = store.get_daily_ors(
        session_dates=[k.session_date.isoformat() for k in keys],
        symbol=symbol,
        interval=interval,
        orb_minutes=orb_minutes,
    )

    out: List[Optional[Dict]] = []
    for k in keys:
        cached = by_date.get(k.session_date.isoformat())
        if cached:
            out.append(cached)
            continue
        out.append(resolve_daily_or(key=k, ts=ts, spec=spec, store=store, provider=provider))
    return out


def resolve_or_rows(
    sessions: Iterable[date],
    spec: FetchSpec,
    ts: TradingSessions,
    orb_minutes: int,
    store,
    provider=None,
) -> List[Optional[Dict]]:
    """Resolve OR rows for a list of session dates (DB-first).

    This is a small compatibility wrapper used by the live tracker.
    It resolves daily Opening Range (OR) rows for *each* session date:
    - First tries the DB cache (sqlite)
    - If missing, fetches intraday bars and computes OR
    - Saves newly computed OR rows back to DB

    Returns a list aligned with `sessions` (missing sessions may be `None`).
    """
    # The live tracker already produced the exact sessions we want; do not
    # expand/shift them here.
    session_dates = list(sessions)
    return resolve_daily_ors(
        session_dates=session_dates,
        symbol=spec.symbol,
        interval=spec.interval,
        orb_minutes=orb_minutes,
        ts=ts,
        spec=spec,
        store=store,
        provider=provider,
    )
