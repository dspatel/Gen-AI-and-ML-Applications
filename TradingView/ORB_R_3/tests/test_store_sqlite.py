
import pandas as pd
from orb_ref.storage.sqlite_store import SQLiteStore, StoreConfig

def test_store_upsert(tmp_path):
    db = tmp_path / "t.sqlite"
    store = SQLiteStore(StoreConfig(db_path=str(db)))

    dfm = pd.DataFrame([{
        "asof_date": "2026-02-05",
        "symbol": "SPY",
        "sessions_requested": 3,
        "sessions_nonempty": 3,
        "sessions_used": 3,
        "sessions_missing_data": "",
        "ref_high": 1.0,
        "ref_low": 0.5,
        "ref_width": 0.5,
        "or_overlap_ratio": 0.2,
        "inflation_factor": 1.1,
        "median_inside_own_or_pct": 0.4,
        "median_range_to_or": 3.0,
        "mean_direction_bias": 0.1,
        "bias_consistency": 0.5,
    }])
    n = store.upsert_daily_metrics(dfm, run_context={"interval":"1m","orb_minutes":30,"historical_days":3,"include_today_or":False})
    assert n == 1

    dfe = pd.DataFrame([{
        "asof_date":"2026-02-05",
        "symbol":"SPY",
        "timestamp":"2026-02-05T14:35:00+00:00",
        "direction":"UP",
        "ref_high":1.0,
        "ref_low":0.5,
        "ref_width":0.5,
        "close_pen":0.1,
        "wick_pen":0.2,
        "body_norm":0.3,
        "range_norm":0.4,
        "label_open_alignment":"Aligned",
        "label_reference_shape":"Tight",
        "label_regime":"Balanced",
        "label_direction_bias":"Neutral",
        "label_breakout_strength":"Clean",
        "message":"hi"
    }])
    n2 = store.upsert_breakout_events(dfe)
    assert n2 == 1

    out = store.read_table("daily_symbol_metrics", limit=5)
    assert len(out) == 1
