
from datetime import datetime
import pandas as pd

from orb_ref.ranges_or import compute_daily_or
from orb_ref.reference_range import build_reference_range
from orb_ref.lookback_behavior import compute_day_behavior, aggregate_behavior


def test_or_reference_behavior_basic():
    idx = pd.date_range("2026-02-05 08:30", periods=60, freq="1min", tz="America/Chicago")
    df = pd.DataFrame({
        "Open": 1.0,
        "High": [i + 1 for i in range(60)],
        "Low": [i for i in range(60)],
        "Close": [i + 0.5 for i in range(60)],
        "Volume": 100,
    }, index=idx)

    or_row = compute_daily_or(df, idx[0].to_pydatetime(), idx[30].to_pydatetime())
    assert or_row and or_row["or_width"] > 0

    ref = build_reference_range([or_row])
    assert ref["ref_high"] >= ref["ref_low"]

    beh_day = compute_day_behavior(df, or_row["or_high"], or_row["or_low"])
    beh = aggregate_behavior([beh_day])
    assert 0.0 <= beh["median_inside_own_or_pct"] <= 1.0
