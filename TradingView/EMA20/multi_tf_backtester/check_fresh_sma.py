import sqlite3
import pandas as pd

conn = sqlite3.connect(r"e:\Machine Learning\TradingView\EMA20\multi_tf_backtester\data\backtest_data.db")
df = pd.read_sql("SELECT date, close FROM daily_bars WHERE symbol = 'SPY' ORDER BY date ASC", conn)
df['sma_200'] = df['close'].rolling(window=200).mean()

latest = df.iloc[-1]
print(f"Latest Date in Fresh DB: {latest['date']}")
print(f"SPY Close: {latest['close']}")
print(f"SPY 200 SMA: {latest['sma_200']}")
print(f"SPY > 200 SMA? {latest['close'] > latest['sma_200']}")
