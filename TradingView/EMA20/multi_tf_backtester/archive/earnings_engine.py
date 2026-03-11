"""
Earnings Engine: Historical EPS Surprise Sentiment + Earnings Calendar Awareness
================================================================================
Fetches quarterly earnings data from yfinance for all portfolio symbols.
Converts EPS surprises into a persistent sentiment signal that the Alpha Rotation
Engine uses to size positions and manage stops between earnings releases.

Also provides an earnings calendar awareness function that signals when a symbol
is approaching its next earnings date, allowing the engine to either:
- Size up for stocks with a history of beats (conviction play)
- Tighten stops for stocks approaching uncertain earnings
"""

import pandas as pd
import numpy as np
import yfinance as yf
import sqlite3
import os
from datetime import timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'portfolio_data.db')


class EarningsEngine:
    def __init__(self):
        print("Initializing Earnings Engine...")
        self.conn = sqlite3.connect(DB_PATH)
        self.earnings_data = {}     # {symbol: DataFrame of historical earnings}
        self.daily_sentiment = {}   # {symbol: DataFrame of daily interpolated sentiment}
        
    def _create_tables(self):
        cur = self.conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS earnings_history (
                symbol TEXT,
                earnings_date TEXT,
                eps_estimate REAL,
                reported_eps REAL,
                surprise_pct REAL,
                sentiment_score REAL,
                PRIMARY KEY (symbol, earnings_date)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS daily_earnings_sentiment (
                symbol TEXT,
                date TEXT,
                sentiment REAL,
                days_to_earnings INTEGER,
                PRIMARY KEY (symbol, date)
            )
        ''')
        self.conn.commit()
        
    def fetch_earnings_data(self, symbols):
        """
        Fetch historical earnings dates with EPS surprise data for all symbols.
        Converts EPS surprise % into a sentiment score (-1 to +1).
        """
        self._create_tables()
        cur = self.conn.cursor()
        
        for sym in symbols:
            print(f"Fetching earnings history for {sym}...")
            try:
                ticker = yf.Ticker(sym)
                # Get as much history as possible
                ed = ticker.get_earnings_dates(limit=40)
                
                if ed is None or ed.empty:
                    print(f"  No earnings data for {sym}")
                    continue
                
                for dt, row in ed.iterrows():
                    eps_est = row.get('EPS Estimate', None)
                    reported = row.get('Reported EPS', None)
                    surprise = row.get('Surprise(%)', None)
                    
                    # Skip future/upcoming earnings (no reported data)
                    if pd.isna(reported):
                        continue
                    
                    # Convert EPS surprise % to a sentiment score (-1 to +1)
                    # Large beats (>20%) -> strong positive
                    # Small beats (0-10%) -> moderate positive
                    # Misses -> negative proportionally
                    if pd.notna(surprise):
                        if surprise > 30:
                            sentiment = 0.9
                        elif surprise > 15:
                            sentiment = 0.7
                        elif surprise > 5:
                            sentiment = 0.5
                        elif surprise > 0:
                            sentiment = 0.3
                        elif surprise > -5:
                            sentiment = -0.2
                        elif surprise > -15:
                            sentiment = -0.5
                        else:
                            sentiment = -0.8
                    else:
                        sentiment = 0.0
                    
                    date_str = dt.strftime('%Y-%m-%d') if hasattr(dt, 'strftime') else str(dt)[:10]
                    
                    cur.execute('''
                        INSERT OR REPLACE INTO earnings_history 
                        (symbol, earnings_date, eps_estimate, reported_eps, surprise_pct, sentiment_score)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (sym, date_str, 
                          float(eps_est) if pd.notna(eps_est) else None,
                          float(reported) if pd.notna(reported) else None,
                          float(surprise) if pd.notna(surprise) else None,
                          sentiment))
                    
            except Exception as e:
                print(f"  Error fetching {sym}: {e}")
                continue
                
        self.conn.commit()
        print("Earnings data fetch complete.")
        
    def build_daily_sentiment(self, symbols, start_date='2019-01-01', end_date='2026-12-31'):
        """
        Interpolates quarterly earnings sentiment into a daily signal.
        After an earnings report:
        - The sentiment persists strongly for ~30 days (market digests the report)
        - Fades gradually over 60-90 days until the next earnings
        - Also calculates days_to_next_earnings for calendar awareness
        """
        self._create_tables()
        cur = self.conn.cursor()
        
        # First, load all earnings history from DB
        df_all = pd.read_sql("SELECT * FROM earnings_history ORDER BY earnings_date", self.conn)
        
        all_dates = pd.bdate_range(start=start_date, end=end_date)
        
        for sym in symbols:
            sym_earnings = df_all[df_all['symbol'] == sym].copy()
            if sym_earnings.empty:
                continue
                
            sym_earnings['earnings_date'] = pd.to_datetime(sym_earnings['earnings_date'])
            sym_earnings = sym_earnings.sort_values('earnings_date')
            
            # Build daily sentiment series
            daily_records = []
            
            for date in all_dates:
                # Find the most recent earnings before this date
                past_earnings = sym_earnings[sym_earnings['earnings_date'] <= date]
                
                if past_earnings.empty:
                    sentiment = 0.0
                    days_to_next = -1
                else:
                    last_earning = past_earnings.iloc[-1]
                    days_since = (date - last_earning['earnings_date']).days
                    base_sentiment = last_earning['sentiment_score']
                    
                    # Decay function: sentiment fades over ~90 days
                    # Strong for first 30 days, then linear decay
                    if days_since <= 30:
                        decay = 1.0  # Full strength for first month
                    elif days_since <= 90:
                        decay = 1.0 - ((days_since - 30) / 60.0)  # Linear fade
                    else:
                        decay = 0.0  # Fully faded after 90 days
                    
                    sentiment = base_sentiment * max(0, decay)
                
                # Calculate days to next earnings
                future_earnings = sym_earnings[sym_earnings['earnings_date'] > date]
                if not future_earnings.empty:
                    days_to_next = (future_earnings.iloc[0]['earnings_date'] - date).days
                else:
                    days_to_next = -1  # Unknown
                
                daily_records.append({
                    'symbol': sym,
                    'date': date.strftime('%Y-%m-%d'),
                    'sentiment': round(sentiment, 4),
                    'days_to_earnings': days_to_next
                })
            
            # Batch insert
            for rec in daily_records:
                cur.execute('''
                    INSERT OR REPLACE INTO daily_earnings_sentiment 
                    (symbol, date, sentiment, days_to_earnings)
                    VALUES (?, ?, ?, ?)
                ''', (rec['symbol'], rec['date'], rec['sentiment'], rec['days_to_earnings']))
                
            print(f"  {sym}: Built {len(daily_records)} days of sentiment from {len(sym_earnings)} earnings")
        
        self.conn.commit()
        print("Daily earnings sentiment database built.")
    
    def load_sentiment_data(self):
        """Load the pre-computed daily earnings sentiment from the database."""
        print("Loading Earnings Sentiment History...")
        df = pd.read_sql("SELECT * FROM daily_earnings_sentiment ORDER BY date", self.conn)
        if df.empty:
            print("  WARNING: No earnings sentiment data found in database.")
            return
            
        df['date'] = pd.to_datetime(df['date'])
        
        for sym, grp in df.groupby('symbol'):
            grp = grp.set_index('date').sort_index()
            self.daily_sentiment[sym] = grp
            
        print(f"  Loaded earnings sentiment for {len(self.daily_sentiment)} symbols")
    
    def get_sentiment(self, symbol, current_date):
        """
        Returns the earnings-based sentiment score for a symbol on a date.
        Range: -1 (Big earnings miss) to +1 (Big earnings beat)
        """
        if symbol not in self.daily_sentiment:
            return 0.0
            
        df = self.daily_sentiment[symbol]
        
        if current_date in df.index:
            return df.loc[current_date, 'sentiment']
            
        # Fallback to most recent date
        past = df[df.index <= current_date]
        if past.empty:
            return 0.0
        return past.iloc[-1]['sentiment']
    
    def days_to_earnings(self, symbol, current_date):
        """
        Returns the number of days until the next expected earnings release.
        Returns -1 if unknown.
        """
        if symbol not in self.daily_sentiment:
            return -1
            
        df = self.daily_sentiment[symbol]
        
        if current_date in df.index:
            return int(df.loc[current_date, 'days_to_earnings'])
            
        past = df[df.index <= current_date]
        if past.empty:
            return -1
        return int(past.iloc[-1]['days_to_earnings'])
    
    def get_earnings_streak(self, symbol, current_date):
        """
        Returns the number of consecutive earnings beats for a symbol 
        leading up to the current date. A stock with 4+ consecutive beats
        is a high-conviction hold through the next earnings.
        """
        df_all = pd.read_sql(
            "SELECT * FROM earnings_history WHERE symbol=? AND earnings_date<=? ORDER BY earnings_date DESC",
            self.conn, params=(symbol, current_date.strftime('%Y-%m-%d') if hasattr(current_date, 'strftime') else str(current_date)[:10])
        )
        
        streak = 0
        for _, row in df_all.iterrows():
            if pd.notna(row['surprise_pct']) and row['surprise_pct'] > 0:
                streak += 1
            else:
                break
        return streak


if __name__ == "__main__":
    symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN',
               'AMD', 'NFLX', 'AVGO', 'CRM', 'ADBE', 'ORCL', 'INTC', 'QCOM', 'SPY']
    
    engine = EarningsEngine()
    
    print("\n=== PHASE 1: Fetching Historical Earnings ===")
    engine.fetch_earnings_data(symbols)
    
    print("\n=== PHASE 2: Building Daily Sentiment Database ===")
    engine.build_daily_sentiment(symbols, start_date='2019-01-01', end_date='2026-03-01')
    
    print("\n=== PHASE 3: Verifying Data ===")
    engine.load_sentiment_data()
    
    import pandas as pd
    test_date = pd.Timestamp('2024-01-15')
    print(f"\nSentiment Scores for {test_date.date()}:")
    for sym in ['NVDA', 'META', 'TSLA', 'AAPL', 'INTC']:
        s = engine.get_sentiment(sym, test_date)
        dte = engine.days_to_earnings(sym, test_date)
        streak = engine.get_earnings_streak(sym, test_date)
        print(f"  {sym:>5} | Sentiment: {s:+.3f} | Days to Earnings: {dte:>3} | Beat Streak: {streak}")
