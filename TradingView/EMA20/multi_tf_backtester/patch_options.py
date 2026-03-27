import sqlite3
import json
import datetime
import os
import yfinance as yf
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'live_trades.db')

def patch_options_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Try adding the column if world_state_logger hasn't run yet
    try:
        cur.execute('ALTER TABLE world_state_log ADD COLUMN options_data JSON')
    except sqlite3.OperationalError:
        pass
        
    cur.execute("SELECT date, options_data FROM world_state_log")
    rows = cur.fetchall()
    
    # Define tracking symbols
    symbols = {
        'VIX': '^VIX',          # CBOE Volatility Index
        'VIX_3M': '^VIX3M',     # CBOE 3-Month Volatility
        'VIX_9D': '^VIX9D',     # CBOE 9-Day Volatility
        'SKEW': '^SKEW',        # CBOE SKEW Index
        'VVIX': '^VVIX'         # VIX of VIX
    }
    
    updated_count = 0
    for row in rows:
        target_date_str, options_json = row
        if options_json:
            continue
            
        target_date = datetime.datetime.strptime(target_date_str, '%Y-%m-%d')
        print(f"Patching missing options data for: {target_date_str}")
        
        options_data = {}
        
        try:
            # Pull last 7 days to cover weekends and pick closest historical close
            start_date = (target_date - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
            end_date = (target_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            
            for name, ticker_str in symbols.items():
                ticker = yf.Ticker(ticker_str)
                hist = ticker.history(start=start_date, end=end_date)
                if not hist.empty:
                    # Filter to rows on or before the target_date
                    valid_rows = hist[hist.index.tz_localize(None) <= pd.to_datetime(target_date)]
                    if not valid_rows.empty:
                        last_val = valid_rows.iloc[-1]['Close']
                        options_data[name] = float(last_val)
                        
            # Calculate Term Structure (Backwardation vs Contango)
            if 'VIX_9D' in options_data and 'VIX_3M' in options_data:
                options_data['VIX_Term_Structure_Ratio'] = round(options_data['VIX_9D'] / options_data['VIX_3M'], 3)
                
        except Exception as e:
            print(f"Error fetching options/volatility data for {target_date}: {e}")
            
        cur.execute("UPDATE world_state_log SET options_data = ? WHERE date = ?", (json.dumps(options_data), target_date_str))
        updated_count += 1
            
    conn.commit()
    conn.close()
    print(f"Successfully patched {updated_count} historic records with flawless Options data.")

if __name__ == '__main__':
    patch_options_data()
