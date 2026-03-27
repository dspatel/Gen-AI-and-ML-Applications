import pandas as pd
from signal_engines import PortfolioDataEngine, MacroEngine, RulesEngine
import datetime
import pytz

db = PortfolioDataEngine()
db.load_all_data()

macro = MacroEngine(db)
rules = RulesEngine(db)

# Since we synthesised the date to 'America/New_York' today
today = pd.to_datetime(pd.Timestamp('today').strftime('%Y-%m-%d'))
print(f"\n--- Engines Test for {today.date()} ---")

spy_df = db.daily['SPY']

if today in spy_df.index:
    row = spy_df.loc[today]
    print(f"SPY Spot Close: ${row['close']:.2f}")
    if pd.notna(row['sma_200']):
        print(f"SPY 200-Day SMA: ${row['sma_200']:.2f}")
    else:
        print("SPY 200-Day SMA: NaN")
else:
    print(f"Date {today.date()} not found in SPY index.")
    
weather = macro.get_weather(today)
print(f"Macro Weather (SPY > 200 SMA): {'BULLISH (Trading ON)' if weather else 'BEARISH (Trading OFF)'}")

if weather:
    leaderboard = rules.score_symbols(today)
    print("\n--- Daily Leaderboard ---")
    print(leaderboard.head(10).to_string(index=False))
