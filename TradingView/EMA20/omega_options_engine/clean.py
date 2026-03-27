import sqlite3
try:
    conn = sqlite3.connect('omega_telemetry.db')
    conn.execute("DELETE FROM options_surface WHERE state_id IN (SELECT state_id FROM underlying_state WHERE timestamp LIKE '2025%')")
    conn.execute("DELETE FROM engine_actions WHERE state_id IN (SELECT state_id FROM underlying_state WHERE timestamp LIKE '2025%')")
    conn.execute("DELETE FROM underlying_state WHERE timestamp LIKE '2025%'")
    conn.commit()
    conn.close()
    print("Database Cleaned")
except Exception as e:
    print(f"Error: {e}")
