import pandas as pd
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'portfolio_data.db')

import requests

def scrape_sp500_symbols():
    """Scrapes the live list of S&P 500 components from Wikipedia."""
    print("Scraping live S&P 500 components from Wikipedia...")
    
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    response = requests.get(url, headers=headers)
    
    import io
    tables = pd.read_html(io.StringIO(response.text))
    df = tables[0]
    
    # Clean the tickers (e.g. replacing dots with dashes for yfinance compatibility)
    symbols = df['Symbol'].str.replace('.', '-').tolist()
    
    # Always include the benchmark and volatility index
    if 'SPY' not in symbols:
        symbols.insert(0, 'SPY')
        
    if 'QQQ' not in symbols:
        symbols.insert(1, 'QQQ')
        
    if '^VIX' not in symbols:
        symbols.insert(2, '^VIX')
        
    print(f"Successfully scraped {len(symbols)} symbols.")
    return symbols

def save_universe_to_db(symbols):
    """Saves the active symbol universe to the database for the Fetcher and Engine to use."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Create the active universe table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS active_universe (
            symbol TEXT PRIMARY KEY
        )
    ''')
    
    # Clear old universe
    cur.execute('DELETE FROM active_universe')
    
    # Insert new universe
    for sym in symbols:
        cur.execute('INSERT INTO active_universe (symbol) VALUES (?)', (sym,))
        
    conn.commit()
    conn.close()
    print("Universe successfully saved to database.")

if __name__ == "__main__":
    live_universe = scrape_sp500_symbols()
    save_universe_to_db(live_universe)
