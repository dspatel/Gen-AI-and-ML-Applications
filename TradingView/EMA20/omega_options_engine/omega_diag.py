import sqlite3
import pandas as pd

print("--- TODAY'S OPTIONS TRADES ---")
try:
    conn = sqlite3.connect('e:/Machine Learning/TradingView/EMA20/omega_options_engine/omega_telemetry.db')
    query = """
        SELECT s.timestamp, s.ticker, a.action_type, a.selected_contract, a.confidence_score
        FROM engine_actions a
        JOIN underlying_state s ON a.state_id = s.state_id
        WHERE s.timestamp LIKE '2026-03-19%'
    """
    df = pd.read_sql(query, conn)
    if not df.empty:
        print(df.to_string())
    else:
        print("No mathematical trades physically authorized today in the database.")
    conn.close()
except Exception as e:
    print(f"DB Error: {e}")
