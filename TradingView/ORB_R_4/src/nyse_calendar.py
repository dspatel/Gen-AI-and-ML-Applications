from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List
from zoneinfo import ZoneInfo
import pandas_market_calendars as mcal

CST = ZoneInfo("America/Chicago")

@dataclass(frozen=True)
class SessionWindow:
    session_dates_cst: List[str]
    start_dt_cst: datetime
    end_dt_cst: datetime

def last_n_sessions(calendar_name: str, n: int, asof_cst: date | None = None) -> List[date]:
    if n <= 0:
        raise ValueError("n must be positive")
    if asof_cst is None:
        asof_cst = datetime.now(CST).date()

    cal = mcal.get_calendar(calendar_name)
    start = asof_cst - timedelta(days=max(30, n * 4))
    sched = cal.schedule(start_date=start.isoformat(), end_date=asof_cst.isoformat())
    if sched.empty:
        raise RuntimeError(f"{calendar_name} schedule returned empty; check calendar/date range.")
    session_dates = [d.date() for d in sched.index.to_pydatetime()]
    if len(session_dates) < n:
        raise RuntimeError(f"Only found {len(session_dates)} sessions; needed {n}")
    return session_dates[-n:]

def build_session_window(calendar_name: str, n: int, session_start: str, session_end: str, asof_cst: date | None = None) -> SessionWindow:
    sess = last_n_sessions(calendar_name, n, asof_cst=asof_cst)
    first = sess[0]
    last = sess[-1]
    sh, sm = [int(x) for x in session_start.split(":")]
    eh, em = [int(x) for x in session_end.split(":")]
    start_dt = datetime.combine(first, time(sh, sm), tzinfo=CST)
    end_dt = datetime.combine(last, time(eh, em), tzinfo=CST)
    return SessionWindow([d.isoformat() for d in sess], start_dt, end_dt)
