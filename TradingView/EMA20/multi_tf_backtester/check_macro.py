import pandas as pd
from signal_engines import PortfolioDataEngine, MacroEngine

db = PortfolioDataEngine()
db.load_all_data()
macro = MacroEngine(db)

test_date = pd.to_datetime('2026-03-23')
try:
    spy_df = db.daily['SPY']
    row = spy_df.loc[test_date]
    print(f"Date: {test_date.date()}")
    print(f"SPY Close: {row['close']}")
    print(f"SPY 200 SMA: {row['sma_200']}")
    print(f"SPY > 200 SMA? {row['close'] > row['sma_200']}")
except KeyError:
    print(f"Date {test_date} not found in SPY index.")
    
    # closest previous date:
    past = spy_df[spy_df.index <= test_date]
    if not past.empty:
        row = past.iloc[-1]
        print(f"Using fallback date: {row.name.date()}")
        print(f"SPY Close: {row['close']}")
        print(f"SPY 200 SMA: {row['sma_200']}")
        print(f"SPY > 200 SMA? {row['close'] > row['sma_200']}")
