import pandas as pd
import numpy as np
import pickle
import warnings
warnings.filterwarnings('ignore')

from adaptive_200 import run_adaptive_engine

print("Running pure VIX-Adaptive Engine from 2016-2026 to extract Year-over-Year (YoY) metrics...")

# Suppress the massive log outputs from the engine itself using a mock logger or simply capture its output dict
engine, df_signals, returns, stats = run_adaptive_engine('2016-01-01', '2026-02-27', 10)

if df_signals is None or df_signals.empty:
    print("Error: Backtest returned empty signals.")
    exit(1)

df_signals['Date'] = pd.to_datetime(df_signals['Date'])
# Ensure sorted
df_signals = df_signals.sort_values('Date')
dates = df_signals['Date'].unique()

print(f"\nExtracted Trading Days: {len(dates)}")
print(f"Total Returns from adaptive_200: {stats['Total Return (%)']}")
print(f"Annualized Return: {stats['Annualized Return (%)']}")
print(f"Max Drawdown: {stats['Max Drawdown (%)']}")

print("\n--- YEAR-OVER-YEAR BLIND TEST PERFORMANCE ---")

# We can infer daily portfolio performance. Since we don't return the exact daily equity curve from adaptive_200,
# we can rebuild the simple portfolio compound value over those dates.
# The engine object holds the executed trades.

from collections import defaultdict
import datetime

# Let's extract trades directly from the engine output or just simulate portfolio equity
initial_capital = 100000.0
portfolio_equity_curve = {}

# Reconstruct equity curve daily
for date in np.sort(df_signals['Date'].unique()):
    dt = pd.to_datetime(date)
    # The 'returns' list from run_adaptive_engine has (entry_date, exit_date, pct_return)
    # We can reconstruct daily equity approximately, or we can just reconstruct the yearly return from closed trades.
    pass

# Alternatively, since we just have the 'returns' array of closed trades:
print("Closed Trades: ", len(returns))
yearly_pnls = defaultdict(list)

# The 'returns' format is: (entry_date, exit_date, symbol, return_pct) - wait I don't know the exact tuple returned by BacktestEngine.run()
# Let's just run an explicit backtester loop to get daily equity. 

# Let's write a small backtest shell here specifically to generate the exact YoY returns.
def get_daily_equity():
    equity = 100000.0
    daily_eq = []
    
    # Simple simulation using the generated df_signals
    date_groups = df_signals.groupby('Date')
    
    for date, group in date_groups:
        # this is exactly what the strategy engine does
        pass

# Let's print out the first 5 returns to see the shape
print("Return Shape:", returns[0] if len(returns) > 0 else "None")
