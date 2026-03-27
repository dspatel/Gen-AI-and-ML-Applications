import sqlite3
import pandas as pd
import os

db_path = r"e:\Machine Learning\TradingView\EMA20\multi_tf_backtester\data\portfolio_data.db"
conn = sqlite3.connect(db_path)
df = pd.read_sql("SELECT * FROM daily WHERE symbol = 'SPY' ORDER BY date DESC LIMIT 10", conn)
print(df.to_string())
