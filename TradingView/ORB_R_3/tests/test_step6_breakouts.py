
import pandas as pd
from orb_ref.breakouts import BreakoutParams, detect_breakouts_close_only
from orb_ref.intensity import compute_breakout_intensity


def _make_df():
    idx = pd.date_range("2026-02-05 08:30", periods=6, freq="1min", tz="America/Chicago")
    df = pd.DataFrame({
        "Open":  [100, 100, 100, 101, 103, 101],
        "High":  [101, 101, 102, 104, 104, 102],
        "Low":   [ 99,  99,  99, 100, 101, 100],
        "Close": [100, 100, 101, 103, 101, 100],  # breakout UP at bar 4 close=103
        "Volume":[1,1,1,1,1,1],
    }, index=idx)
    return df

def test_close_only_breakout_and_rearm():
    df = _make_df()
    ref_low, ref_high = 99.5, 102.0
    params = BreakoutParams(close_required=True, inside_reset_pct=0.10)
    ev = detect_breakouts_close_only(df, ref_low, ref_high, params=params)
    assert len(ev) == 1
    assert ev[0]["direction"] == "UP"

def test_intensity_up():
    df = _make_df()
    ref_low, ref_high = 99.5, 102.0
    width = ref_high - ref_low
    ts = df.index[3]
    intensity = compute_breakout_intensity(df.loc[ts], ref_low, ref_high, width, "UP")
    assert intensity["close_pen"] > 0
    assert intensity["wick_pen"] > 0
    assert intensity["body_norm"] >= 0
