import sqlite3
import pandas as pd

conn = sqlite3.connect(r"e:\Machine Learning\TradingView\EMA20\multi_tf_backtester\data\portfolio_data.db")
df = pd.read_sql("SELECT symbol, date, close FROM daily WHERE symbol IN ('SPY', 'AAPL') ORDER BY date DESC LIMIT 20", conn)
print(df.to_string())
