import yfinance as yf
import json
import pandas as pd

t = yf.Ticker('NVDA')

print("=== NEWS DATA ===")
try:
    news = t.news
    print(f"Number of news items: {len(news)}")
    if news:
        for n in news[:3]:
            title = n.get('title', '')
            if not title and 'content' in n:
                title = n['content'].get('title', 'N/A') 
            pub = n.get('providerPublishTime', 0)
            if not pub and 'content' in n:
                pub = n['content'].get('pubDate', 'N/A')
            if isinstance(pub, (int, float)) and pub > 0:
                pub = pd.to_datetime(pub, unit='s')
            print(f"  {pub} | {title}")
        print("\nFirst item keys:", list(news[0].keys()))
except Exception as e:
    print(f"News error: {e}")

print("\n=== EARNINGS DATES ===")
try:
    ed = t.get_earnings_dates(limit=20)
    print(f"Type: {type(ed)}")
    print(ed.head(10))
except Exception as e:
    print(f"Earnings dates error: {e}")

print("\n=== QUARTERLY EARNINGS ===")
try:
    qe = t.quarterly_earnings
    print(f"Type: {type(qe)}")
    print(qe)
except Exception as e:
    print(f"Quarterly earnings error: {e}")
