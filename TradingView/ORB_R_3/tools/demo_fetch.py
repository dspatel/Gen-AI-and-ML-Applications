import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


from datetime import date
from orb_ref.data_fetch import FetchSpec, fetch_lookback_bundle

if __name__ == "__main__":
    spec = FetchSpec(
        symbol="SPY",
        asof_date=date(2026, 2, 5),
        interval="1m",
        cache_dir="cache",
        use_cache=True,
    )

    sessions, frames = fetch_lookback_bundle(
        spec,
        historical_days=5,
        include_today_or=True,
    )

    print("Sessions:", sessions)
    for d, df in frames.items():
        print(d, "rows:", len(df), "first:", df.index.min(), "last:", df.index.max())
