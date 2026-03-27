import sqlite3
import pandas as pd

conn = sqlite3.connect('data/portfolio_data.db')
df = pd.read_sql("SELECT symbol, date, close FROM daily WHERE symbol='SPY' ORDER BY date DESC LIMIT 5", conn)
print("SPY from database:")
print(df)

df_vix = pd.read_sql("SELECT symbol, date, close FROM daily WHERE symbol='^VIX' ORDER BY date DESC LIMIT 5", conn)
print("\nVIX from database:")
print(df_vix)
