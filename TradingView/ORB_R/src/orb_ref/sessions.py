
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as xcals


@dataclass(frozen=True)
class SessionBounds:
    session_date: date
    open_dt: datetime
    close_dt: datetime


class TradingSessions:
    """Trading-session helper based on an exchange calendar (XNYS default).

    Compatible with multiple exchange_calendars APIs/column names.
    All returned datetimes are timezone-aware and converted to requested tz.
    """

    def __init__(self, exchange: str = "XNYS", tz: str = "America/Chicago"):
        self.cal = xcals.get_calendar(exchange)
        self.tz = ZoneInfo(tz)

    def is_trading_day(self, d: date) -> bool:
        return self.cal.is_session(d.isoformat())

    def get_session_bounds(self, d: date) -> SessionBounds:
        s = d.isoformat()
        if not self.cal.is_session(s):
            raise ValueError(f"{d} is not a trading session for {self.cal.name}")

        # Get row for session, cross-version
        if hasattr(self.cal, "session_schedule"):
            sched = self.cal.session_schedule(s, s)
            row = sched.iloc[0]
        else:
            row = self.cal.schedule.loc[s]

        # Column names differ by version
        if "market_open" in row.index:
            open_utc = row["market_open"]
            close_utc = row["market_close"]
        else:
            open_utc = row["open"]
            close_utc = row["close"]

        open_dt = open_utc.tz_convert(self.tz).to_pydatetime()
        close_dt = close_utc.tz_convert(self.tz).to_pydatetime()
        return SessionBounds(session_date=d, open_dt=open_dt, close_dt=close_dt)

    def get_prev_sessions(self, d: date, n: int) -> list[date]:
        if n <= 0:
            return []
        anchor = d if self.is_trading_day(d) else self._previous_session(d)
        out: list[date] = []
        cur = anchor
        while len(out) < n:
            cur = self._previous_session(cur)
            out.append(cur)
        return out

    def get_or_window_bounds(self, d: date, orb_minutes: int) -> tuple[datetime, datetime]:
        b = self.get_session_bounds(d)
        start = b.open_dt
        end = start + timedelta(minutes=int(orb_minutes))
        return start, end

    def _previous_session(self, d: date) -> date:
        cur = d - timedelta(days=1)
        while not self.is_trading_day(cur):
            cur -= timedelta(days=1)
        return cur
