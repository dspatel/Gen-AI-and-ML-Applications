import sqlite3
import os
from datetime import datetime

db_path = r'e:\Machine Learning\TradingView\EMA20\omega_options_engine\omega_telemetry.db'

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in c.fetchall()]
    
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"Cleaning Omega Telemetry for: {today}")
    
    if 'live_trades' in tables:
        c.execute("DELETE FROM live_trades WHERE timestamp LIKE ?", (f"{today}%",))
        print(f"Deleted {c.rowcount} corrupt wash loop trades from 'live_trades'.")
    if 'underlying_state' in tables:
        c.execute("DELETE FROM underlying_state WHERE timestamp LIKE ?", (f"{today}%",))
        print(f"Deleted {c.rowcount} stale records from 'underlying_state'.")
    if 'options_surface' in tables:
        c.execute("DELETE FROM options_surface WHERE state_id NOT IN (SELECT state_id FROM underlying_state)")
        print(f"Deleted {c.rowcount} orphaned options surface records.")
        
    conn.commit()
    conn.close()
    print("Database purification complete.")
else:
    print("omega_telemetry.db Database not found yet.")
