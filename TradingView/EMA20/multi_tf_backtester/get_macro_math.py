from signal_engines import PortfolioDataEngine
import pandas as pd

db = PortfolioDataEngine()
db.load_all_data()

spy_df = db.daily['SPY']

# The execution ran on March 16th, so we pull exactly that date
today_dt = pd.to_datetime('2026-03-16')

if today_dt in spy_df.index:
    row = spy_df.loc[today_dt]
    print(f"--- MATHEMATICAL SNAPSHOT FOR {today_dt.date()} ---")
    print(f"SPY Spot Close: ${row['close']:.2f}")
    print(f"SPY 200-Day SMA: ${row['sma_200']:.2f}")
    
    if row['close'] > row['sma_200']:
        print("Status: BULLISH (Spot > 200 SMA)")
    else:
        print("Status: BEARISH (Spot < 200 SMA)")
else:
    # Fallback if the date string is formatted differently
    row = spy_df.iloc[-1]
    print(f"--- MATHEMATICAL SNAPSHOT FOR {row.name.date()} ---")
    print(f"SPY Spot Close: ${row['close']:.2f}")
    print(f"SPY 200-Day SMA: ${row['sma_200']:.2f}")
