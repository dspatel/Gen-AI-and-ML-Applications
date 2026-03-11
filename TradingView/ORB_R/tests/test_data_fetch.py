
from datetime import date, datetime
import pandas as pd

from orb_ref.data_fetch import FetchSpec, fetch_session_bars
from orb_ref.sessions import TradingSessions


class FakeProvider:
    def fetch(self, symbol: str, start_dt: datetime, end_dt: datetime, interval: str) -> pd.DataFrame:
        # Build in same tz as inputs, then convert to UTC (provider-like)
        idx = pd.date_range(start=start_dt, end=end_dt, freq="1min")
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        else:
            idx = idx.tz_convert("UTC")

        return pd.DataFrame(
            {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 100},
            index=idx,
        )


def test_fetch_session_bars_slices_to_session(tmp_path):
    d = date(2026, 2, 5)
    spec = FetchSpec(symbol="SPY", asof_date=d, interval="1m", cache_dir=str(tmp_path), use_cache=False)

    ts = TradingSessions()
    bounds = ts.get_session_bounds(d)

    df = fetch_session_bars(spec, d, provider=FakeProvider())
    assert not df.empty
    assert df.index.tz is not None
    assert df.index.min() >= bounds.open_dt
    assert df.index.max() <= bounds.close_dt
