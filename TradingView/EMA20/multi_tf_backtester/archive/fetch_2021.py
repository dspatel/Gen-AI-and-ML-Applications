import yfinance as yf
import sqlite3
import pandas as pd
import os

DB_PATH = 'data/backtest_data.db'

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS monthly_bars (
            symbol TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date)
        )
    ''')
    conn.commit()
    conn.close()

def fetch_history(symbols, start_date='2021-01-01'):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    for symbol in symbols:
        print(f'Fetching extended daily for {symbol}...')
        try:
            daily = yf.download(symbol, start=start_date, interval='1d', progress=False)
            if daily.empty: continue
            daily.reset_index(inplace=True)
            if isinstance(daily.columns, pd.MultiIndex):
                daily.columns = ['_'.join(c).strip('_') if c[1] else c[0] for c in daily.columns]
                rename_map = {'Date': 'date'}
                for c in daily.columns:
                    if c.startswith('Open_'): rename_map[c] = 'open'
                    if c.startswith('High_'): rename_map[c] = 'high'
                    if c.startswith('Low_'): rename_map[c] = 'low'
                    if c.startswith('Close_'): rename_map[c] = 'close'
                    if c.startswith('Volume_'): rename_map[c] = 'volume'
                daily.rename(columns=rename_map, inplace=True)
            else:
                daily.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                
            cur.execute('DELETE FROM daily_bars WHERE symbol=?', (symbol,))
            for idx, row in daily.iterrows():
                cur.execute("""
                    INSERT INTO daily_bars (symbol, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, str(row['date'].date()), row['open'], row['high'], row['low'], row['close'], row['volume']))
                
            print(f'Fetching extended monthly for {symbol}...')
            monthly = yf.download(symbol, start=start_date, interval='1mo', progress=False)
            if monthly.empty: continue
            monthly.reset_index(inplace=True)
            if isinstance(monthly.columns, pd.MultiIndex):
                monthly.columns = ['_'.join(c).strip('_') if c[1] else c[0] for c in monthly.columns]
                rename_map = {'Date': 'date'}
                for c in monthly.columns:
                    if c.startswith('Open_'): rename_map[c] = 'open'
                    if c.startswith('High_'): rename_map[c] = 'high'
                    if c.startswith('Low_'): rename_map[c] = 'low'
                    if c.startswith('Close_'): rename_map[c] = 'close'
                    if c.startswith('Volume_'): rename_map[c] = 'volume'
                monthly.rename(columns=rename_map, inplace=True)
            else:
                monthly.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
            
            cur.execute('DELETE FROM monthly_bars WHERE symbol=?', (symbol,))
            for idx, row in monthly.iterrows():
                cur.execute("""
                    INSERT INTO monthly_bars (symbol, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, str(row['date'].date()), row['open'], row['high'], row['low'], row['close'], row['volume']))
        except Exception as e:
            print(f"Error on {symbol}: {e}")
            
    conn.commit()
    conn.close()
    print('Done.')

if __name__ == '__main__':
    init_db()
    fetch_history(['QQQ', 'QLD', 'SQQQ', 'PSQ'])
