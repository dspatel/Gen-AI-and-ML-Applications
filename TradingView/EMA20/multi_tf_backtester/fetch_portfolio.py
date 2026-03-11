import yfinance as yf
import sqlite3
import pandas as pd
import os

DB_PATH = 'data/portfolio_data.db'

def get_active_universe():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT symbol FROM active_universe", conn)
        return df['symbol'].tolist()
    except Exception as e:
        print(f"Warning: Could not load active universe from DB ({e}). Falling back to SPY only.")
        return ['SPY']
    finally:
        conn.close()

def fetch_portfolio(start_date='2018-01-01'):
    symbols = get_active_universe()
    if not symbols: return
    print(f"Fetching data for {len(symbols)} symbols from active universe...")
    
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily (
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
    
    for symbol in symbols:
        print(f'Fetching {symbol}...')
        try:
            df = yf.download(symbol, start=start_date, interval='1d', progress=False)
            if df.empty: continue
            df.reset_index(inplace=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = ['_'.join(c).strip('_') if c[1] else c[0] for c in df.columns]
                rename_map = {'Date': 'date'}
                for c in df.columns:
                    if c.startswith('Open_'): rename_map[c] = 'open'
                    if c.startswith('High_'): rename_map[c] = 'high'
                    if c.startswith('Low_'): rename_map[c] = 'low'
                    if c.startswith('Close_'): rename_map[c] = 'close'
                    if c.startswith('Volume_'): rename_map[c] = 'volume'
                df.rename(columns=rename_map, inplace=True)
            else:
                df.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
                
            cur.execute('DELETE FROM daily WHERE symbol=?', (symbol,))
            for idx, row in df.iterrows():
                cur.execute("""
                    INSERT INTO daily (symbol, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (symbol, str(row['date'].date()), row['open'], row['high'], row['low'], row['close'], row['volume']))
        except Exception as e:
            print(f'Failed {symbol}: {e}')
            
    conn.commit()
    conn.close()
    print('Done fetching portfolio data.')

if __name__ == "__main__":
    fetch_portfolio()
