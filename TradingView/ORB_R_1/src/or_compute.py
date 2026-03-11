from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import Any, Dict, Optional, Tuple
import pandas as pd
from zoneinfo import ZoneInfo

from .data_fetch import fetch_intraday_yfinance
from .calendar import TradingCalendar

@dataclass(frozen=True)
class ORResult:
    session_date: str
    symbol: str
    or_start: datetime
    or_end: datetime
    or_high: float
    or_low: float
    or_width: float

def compute_or_from_intraday(df_utc: pd.DataFrame, or_start_local: datetime, or_end_local: datetime, market_tz: str) -> ORResult | None:
    if df_utc is None or df_utc.empty:
        return None
    tz = ZoneInfo(market_tz)
    # convert index to market tz
    df = df_utc.copy()
    df.index = df.index.tz_convert(tz)
    window = df[(df.index >= or_start_local) & (df.index < or_end_local)]
    if window.empty:
        return None
    or_high = float(window["high"].max())
    or_low = float(window["low"].min())
    return ORResult(
        session_date=or_start_local.date().isoformat(),
        symbol="",
        or_start=or_start_local,
        or_end=or_end_local,
        or_high=or_high,
        or_low=or_low,
        or_width=float(or_high - or_low),
    )

def resolve_daily_or(db, cal: TradingCalendar, symbol: str, session_date: date, interval: str, orb_minutes: int) -> Tuple[Optional[Dict[str, Any]], str]:
    """DB-first OR resolution. Never raises for missing data; returns (row|None, reason)."""
    d_str = session_date.isoformat()
    cached = db.get_daily_or(d_str, symbol)
    if cached and cached.get("or_high") is not None and cached.get("or_low") is not None:
        return cached, "cache_hit"

    sess = cal.session_for_date(session_date)
    if sess is None:
        return None, "not_a_session_day"

    or_start = sess.open_ts
    or_end = sess.open_ts + timedelta(minutes=int(orb_minutes))

    # Fetch intraday in UTC bounds
    start_utc = or_start.astimezone(ZoneInfo("UTC"))
    end_utc = sess.close_ts.astimezone(ZoneInfo("UTC"))
    try:
        df = fetch_intraday_yfinance(symbol, start_utc=start_utc, end_utc=end_utc, interval=interval)
    except Exception as e:
        # Include the exception message for audit/debuggability.
        msg = str(e).replace("\n", " ").strip()
        if len(msg) > 160:
            msg = msg[:157] + "..."
        return None, f"intraday_fetch_failed: {type(e).__name__}: {msg}"

    or_res = compute_or_from_intraday(df, or_start, or_end, market_tz=str(cal.tz))
    if or_res is None:
        return None, "or_window_missing_bars"

    row = {
        "session_date": d_str,
        "symbol": symbol,
        "or_start": or_start.isoformat(),
        "or_end": or_end.isoformat(),
        "or_high": or_res.or_high,
        "or_low": or_res.or_low,
        "or_width": or_res.or_width,
        "interval": interval,
        "orb_minutes": int(orb_minutes),
        "source": "computed_from_intraday",
        "created_at": None,
    }
    try:
        db.upsert_daily_or(row)
    except Exception as e:
        # Even DB write failure must not crash higher layers; return computed but note write issue.
        return row, f"computed_but_db_write_failed: {type(e).__name__}"

    return row, "computed_and_stored"
