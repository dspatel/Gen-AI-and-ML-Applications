import sqlite3
import pandas as pd

print("--- portfolio_data.db ---")
try:
    conn1 = sqlite3.connect(r"e:\Machine Learning\TradingView\EMA20\multi_tf_backtester\data\portfolio_data.db")
    df1 = pd.read_sql("SELECT date, close FROM daily WHERE symbol = 'SPY' ORDER BY date DESC LIMIT 5", conn1)
    print(df1)
except Exception as e:
    print(e)

print("\n--- backtest_data.db ---")
try:
    conn2 = sqlite3.connect(r"e:\Machine Learning\TradingView\EMA20\multi_tf_backtester\data\backtest_data.db")
    df2 = pd.read_sql("SELECT date, close FROM daily_bars WHERE symbol = 'SPY' ORDER BY date DESC LIMIT 5", conn2)
    print(df2)
except Exception as e:
    print(e)
