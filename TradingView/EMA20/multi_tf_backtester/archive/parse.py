import re

with open('hybrid_results.txt', 'r', encoding='utf-16-le', errors='replace') as f:
    text = f.read()

# find the final summary section
try:
    summary = text.split("HYBRID MOMENTUM RESULTS")[1]
    lines = summary.split('\n')
    for line in lines:
        if any(line.startswith(s) for s in ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'META', 'VOO', 'SCHG', 'AMZN']):
            print(line.strip())
except Exception as e:
    print(e)
