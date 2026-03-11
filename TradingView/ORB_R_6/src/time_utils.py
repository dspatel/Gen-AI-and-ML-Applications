from __future__ import annotations

from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

CST = ZoneInfo("America/Chicago")


def parse_cst_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD into CST midnight."""
    return datetime.fromisoformat(date_str).replace(tzinfo=CST)


def combine_cst_date_time(date_str: str, hhmm: str) -> datetime:
    """Combine YYYY-MM-DD and 'HH:MM' into CST datetime."""
    d = parse_cst_date(date_str)
    hh, mm = hhmm.split(":")
    return d.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
