import sqlite3
import os
import pandas as pd
from datetime import datetime
import json

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'live_trades.db')

def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_telemetry_db():
    """Initializes the production SQLite trade ledger."""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS executed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            action TEXT,
            symbol TEXT,
            quantity REAL,
            estimated_price REAL,
            roc_score REAL,
            vix_level REAL,
            macro_bullish INTEGER,
            state_context TEXT
        )
    ''')
    
    # Seamless migration for existing databases
    try:
        cur.execute("ALTER TABLE executed_trades ADD COLUMN state_context TEXT")
    except sqlite3.OperationalError:
        pass
        
    # Migration for daily_leaderboard fundamentals
    try:
        cur.execute("ALTER TABLE daily_leaderboard ADD COLUMN fundamentals TEXT")
    except sqlite3.OperationalError:
        pass
        
    cur.execute('''
        CREATE TABLE IF NOT EXISTS daily_leaderboard (
            date TEXT,
            symbol TEXT,
            rank INTEGER,
            roc_score REAL,
            state_context TEXT,
            fundamentals TEXT,
            PRIMARY KEY (date, symbol)
        )
    ''')
        
    conn.commit()
    conn.close()

def log_leaderboard(target_date, leaderboard_data):
    """
    Shadow Logger: Logs the top ranked symbols and their contexts for the day, 
    so the AI can study what it DIDN'T buy.
    leaderboard_data: list of dicts [{'symbol': '...', 'rank': 1, 'score': 0.5, 'context': {...}}]
    """
    try:
        conn = _get_db()
        cur = conn.cursor()
        date_str = str(target_date)
        for row in leaderboard_data:
            state_str = json.dumps(row.get('context', {}))
            cur.execute('''
                INSERT OR REPLACE INTO daily_leaderboard 
                (date, symbol, rank, roc_score, state_context)
                VALUES (?, ?, ?, ?, ?)
            ''', (date_str, row['symbol'], row['rank'], float(row['score']), state_str))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging leaderboard: {e}")

def log_trade(action, symbol, quantity, estimated_price, roc_score=None, vix_level=None, macro_bullish=True, state_context=None):
    """
    Records a live execution decision to the internal production telemetry database.
    """
    try:
        conn = _get_db()
        cur = conn.cursor()
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Convert booleans and handle Nones
        macro_val = 1 if macro_bullish else 0
        roc = float(roc_score) if roc_score is not None else 0.0
        vix = float(vix_level) if vix_level is not None else 0.0
        
        state_str = json.dumps(state_context) if state_context else "{}"
        
        cur.execute('''
            INSERT INTO executed_trades 
            (timestamp, action, symbol, quantity, estimated_price, roc_score, vix_level, macro_bullish, state_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (timestamp, action.upper(), symbol, float(quantity), float(estimated_price), roc, vix, macro_val, state_str))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging telemetry for {symbol}: {e}")

def get_recent_telemetry(days=30):
    """Retrieves standard trade logs for reporting / analysis."""
    try:
        conn = _get_db()
        df = pd.read_sql(f"SELECT * FROM executed_trades ORDER BY timestamp DESC LIMIT 500", conn)
        conn.close()
        
        if df.empty:
            return pd.DataFrame()
            
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Filter by recent days if requested
        if days:
            cutoff = datetime.now() - pd.Timedelta(days=days)
            df = df[df['timestamp'] >= cutoff]
            
        return df
    except:
        return pd.DataFrame()

# Initialize the database table automatically when this module is imported.
init_telemetry_db()
