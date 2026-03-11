import sqlite3
import yfinance as yf
import json
import os
import time

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'live_trades.db')

def fetch_fundamentals(symbol):
    """
    Fetches the 6th dimension (business corporate health) for a specific symbol.
    """
    print(f"Fetching corporate health for {symbol}...")
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        # We only care about high-impact baseline growth metrics and sector categorization
        fundamentals = {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "revenueGrowth": info.get("revenueGrowth"),
            "profitMargins": info.get("profitMargins"),
            "debtToEquity": info.get("debtToEquity"),
            "returnOnEquity": info.get("returnOnEquity"),
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "trailingEps": info.get("trailingEps"),
            "forwardEps": info.get("forwardEps"),
            "freeCashflow": info.get("freeCashflow"),
            "beta": info.get("beta")
        }
        return fundamentals
    except Exception as e:
        print(f"Error fetching fundamentals for {symbol}: {e}")
        return None

def run_fundamentals_logger():
    # Attempt to add the column if it doesn't exist, just in case
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE daily_leaderboard ADD COLUMN fundamentals TEXT")
    except sqlite3.OperationalError:
        pass # Column already exists
    conn.commit()

    # Find all unique symbols in the ledger that are missing their fundamentals or sector data
    cur.execute("SELECT DISTINCT symbol FROM daily_leaderboard WHERE fundamentals IS NULL OR json_extract(fundamentals, '$.sector') IS NULL")
    missing_symbols = [row[0] for row in cur.fetchall()]
    
    if not missing_symbols:
        print("All leaderboard entries already have fundamental data attached.")
        conn.close()
        return
        
    print(f"Discovered {len(missing_symbols)} completely blank companies in the ledger.")
    print("Initiating full fundamental backfill...")
    
    updated_count = 0
    for symbol in missing_symbols:
        data = fetch_fundamentals(symbol)
        if data:
            json_data = json.dumps(data)
            # Update Every single historical day this symbol appeared where it has no data or no sector
            cur.execute("""
                UPDATE daily_leaderboard 
                SET fundamentals = ? 
                WHERE symbol = ? AND (fundamentals IS NULL OR json_extract(fundamentals, '$.sector') IS NULL)
            """, (json_data, symbol))
            conn.commit()
            updated_count += 1
            print(f"[{symbol}] Fundamentals injected perfectly into the ledger.")
            time.sleep(1) # Prevent Yahoo Finance rate limits
            
    conn.close()
    print(f"\nFundamental Logging Complete. Synthesized {updated_count} companies.")

if __name__ == "__main__":
    run_fundamentals_logger()
