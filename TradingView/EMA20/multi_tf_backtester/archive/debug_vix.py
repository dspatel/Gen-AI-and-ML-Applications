import pandas as pd
from signal_engines import PortfolioDataEngine

db = PortfolioDataEngine()
data = db.load_all_data()

print(f"Total symbols loaded: {len(data)}")
if '^VIX' in data:
    vix = data['^VIX']
    print(vix[['close', 'vix_level', 'adaptive_sma', 'sma_50']].tail(5))
else:
    print("WARNING: ^VIX is NOT in the database.")
    
# Check a normal symbol
if 'AAPL' in data:
    aapl = data['AAPL']
    print("\nAAPL adaptive metrics check:")
    print(aapl[['close', 'vix_level', 'adaptive_sma', 'sma_50']].tail(5))
