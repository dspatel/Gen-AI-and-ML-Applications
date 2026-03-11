from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import List, Optional, Tuple
import pandas_market_calendars as mcal
import pandas as pd
from zoneinfo import ZoneInfo

@dataclass(frozen=True)
class SessionTimes:
    session_date: str              # YYYY-MM-DD
    open_ts: datetime              # tz-aware
    close_ts: datetime             # tz-aware

class TradingCalendar:
    def __init__(self, exchange: str = "XNYS", timezone: str = "America/Chicago"):
        self.exchange = exchange
        self.tz = ZoneInfo(timezone)
        self.cal = mcal.get_calendar(exchange)

    def is_session(self, d: date) -> bool:
        sched = self.cal.schedule(start_date=d, end_date=d)
        return len(sched) == 1

    def session_times(self, d: date) -> SessionTimes:
        sched = self.cal.schedule(start_date=d, end_date=d)
        if len(sched) != 1:
            raise ValueError(f"Not a session day: {d}")
        # pandas_market_calendars returns UTC timestamps for market_open/close
        open_utc = sched.iloc[0]["market_open"].to_pydatetime()
        close_utc = sched.iloc[0]["market_close"].to_pydatetime()
        open_local = open_utc.astimezone(self.tz)
        close_local = close_utc.astimezone(self.tz)
        return SessionTimes(session_date=d.isoformat(), open_ts=open_local, close_ts=close_local)

    def previous_sessions(self, asof: date, n: int) -> List[date]:
        # get a bit of buffer
        start = asof - timedelta(days=max(10, n * 5))
        sched = self.cal.schedule(start_date=start, end_date=asof - timedelta(days=1))
        # schedule index is tz-aware; convert to date
        days = [ts.date() for ts in sched.index.to_pydatetime()]
        if len(days) < n:
            return days
        return days[-n:]

    def session_for_date(self, d: date) -> Optional[SessionTimes]:
        try:
            return self.session_times(d)
        except Exception:
            return None
