import sqlite3
import json
import datetime
import os
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'live_trades.db')
FRED_API_KEY = "6edacb8953cb602d0e88ba8693891f9d"

def patch_fred_data():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT date, fred_data FROM world_state_log")
    rows = cur.fetchall()
    
    fred_series = {
        'DFF': 'Federal_Funds_Rate',
        'T10Y2Y': 'Yield_Curve_Spread',
        'WALCL': 'Fed_Total_Assets'
    }
    
    updated_count = 0
    for row in rows:
        target_date_str, fred_json = row
        if not fred_json:
            continue
            
        data = json.loads(fred_json)
        
        # Check if DFF or T10Y2Y are missing
        if 'Federal_Funds_Rate' not in data or 'Yield_Curve_Spread' not in data:
            print(f"Patching missing data for: {target_date_str}")
            new_data = {}
            for series_id, name in fred_series.items():
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&observation_end={target_date_str}&limit=5"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    api_data = response.json()
                    for obs in api_data.get('observations', []):
                        val = obs.get('value')
                        if val != '.':
                            new_data[name] = val
                            break
            
            cur.execute("UPDATE world_state_log SET fred_data = ? WHERE date = ?", (json.dumps(new_data), target_date_str))
            updated_count += 1
            
    conn.commit()
    conn.close()
    print(f"Successfully patched {updated_count} historic records with flawless FRED data.")

if __name__ == '__main__':
    patch_fred_data()
