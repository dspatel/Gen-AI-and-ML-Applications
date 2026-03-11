
from datetime import date
from orb_ref.sessions import TradingSessions


def test_weekend_skips():
    ts = TradingSessions()
    prev = ts.get_prev_sessions(date(2026, 2, 7), 1)[0]  # Saturday
    assert ts.is_trading_day(prev)


def test_monday_lookback():
    ts = TradingSessions()
    prev = ts.get_prev_sessions(date(2026, 2, 9), 1)[0]  # Monday
    assert ts.is_trading_day(prev)
