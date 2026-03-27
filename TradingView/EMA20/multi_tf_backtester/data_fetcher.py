import os
import sqlite3
import pandas as pd
from datetime import datetime
import json
import logging
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
import requests
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')
ACCOUNTS_PATH = os.path.join(os.path.dirname(__file__), 'alpaca_accounts.json')

def get_db_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Table for Daily bars
    c.execute('''
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
    
    # Table for Monthly bars
    c.execute('''
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

    # Table for 15-minute bars
    c.execute('''
        CREATE TABLE IF NOT EXISTS intraday_15m_bars (
            symbol TEXT,
            datetime TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, datetime)
        )
    ''')

    # Table for 1-hour bars
    c.execute('''
        CREATE TABLE IF NOT EXISTS intraday_1h_bars (
            symbol TEXT,
            datetime TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, datetime)
        )
    ''')
    
    conn.commit()
    conn.close()

def compute_ema(df, period=20):
    """Calculates EMA exactly as EMA20 needs it."""
    # We use adjust=False to match standard trading platform EMAs
    return df['close'].ewm(span=period, adjust=False).mean()

def _get_alpaca_client():
    """Creates an Alpaca StockHistoricalDataClient using SIP Real Money credentials."""
    try:
        with open(ACCOUNTS_PATH, 'r') as f:
            accounts_data = json.load(f)
            
        for acct in accounts_data.get('accounts', []):
            if acct.get('name') == 'Live Real Money':
                # Pass API keys for the live SIP account
                return StockHistoricalDataClient(acct['key'], acct['secret'])
                
        logging.error("Could not find 'Live Real Money' account in alpaca_accounts.json.")
        return None
    except Exception as e:
        logging.error(f"Failed to initialize Alpaca client: {e}")
        return None

def _fetch_alpaca_bars(symbol, timeframe_obj, start_date, end_date):
    """Generic Alpaca bar fetcher utilizing SIP feed."""
    client = _get_alpaca_client()
    if client is None:
        return None
    
    try:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe_obj,
            start=datetime.strptime(start_date, '%Y-%m-%d'),
            end=datetime.strptime(end_date, '%Y-%m-%d'),
            adjustment='raw' # 'raw' keeps actual prices for exact market replay
        )
        barset = client.get_stock_bars(request)
        if not barset.data:
            return pd.DataFrame()
            
        bars_df = barset.df
    except Exception as e:
        logging.error(f"Error fetching data from Alpaca for {symbol}: {e}")
        return None
    
    if bars_df.empty:
        return pd.DataFrame()
    
    bars_df = bars_df.reset_index()
    time_col = 'timestamp' if 'timestamp' in bars_df.columns else 'time'
    
    result = pd.DataFrame({
        'symbol': symbol,
        'datetime': bars_df[time_col].dt.tz_convert('America/New_York'),
        'open': bars_df['open'],
        'high': bars_df['high'],
        'low': bars_df['low'],
        'close': bars_df['close'],
        'volume': bars_df['volume']
    })
    return result

def _get_alpaca_headers():
    try:
        with open(ACCOUNTS_PATH, 'r') as f:
            accounts_data = json.load(f)
        for acct in accounts_data.get('accounts', []):
            if acct.get('name') == 'Live Real Money':
                return {'APCA-API-KEY-ID': acct['key'], 'APCA-API-SECRET-KEY': acct['secret']}
    except Exception:
        pass
    return None

def _get_today_live_bar(symbol):
    """Fetches the real-time Snapshot of the current day to synthesize a partial Daily Bar."""
    headers = _get_alpaca_headers()
    if not headers: return None
    try:
        url = f"https://data.alpaca.markets/v2/stocks/snapshots?symbols={symbol}"
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            data = res.json()
            snap = data.get(symbol, {})
            db = snap.get('dailyBar')
            if db:
                now = datetime.now(pytz.timezone('America/New_York'))
                # Get the absolute most recent trade price
                latest = snap.get('latestTrade', {}).get('p', db['c'])
                return pd.DataFrame({
                    'symbol': [symbol],
                    'datetime': [now],
                    'open': [db['o']],
                    'high': [max(db['h'], latest)],
                    'low': [min(db['l'], latest)],
                    'close': [latest],
                    'volume': [db['v']]
                })
    except Exception as e:
        logging.error(f"Live snapshot fallback failed for {symbol}: {e}")
    return None

def fetch_and_store_daily(symbol, start_date="2023-01-01", end_date="2025-12-31"):
    logging.info(f"Fetching Daily SIP data for {symbol}...")
    
    # Needs to fetch from 2022 to cover 200 SMA lookbacks smoothly
    actual_start = "2022-01-01"
    
    df = _fetch_alpaca_bars(symbol, TimeFrame(1, TimeFrameUnit.Day), actual_start, end_date)
    
    # --- HOTFIX: Inject Live Snapshot if Today's Bar is missing ---
    today_str = datetime.now(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')
    has_today = False
    if df is not None and not df.empty:
        df['date_check'] = df['datetime'].dt.strftime('%Y-%m-%d')
        if today_str in df['date_check'].values:
            has_today = True
        df = df.drop(columns=['date_check'])
        
    if not has_today:
        live_bar = _get_today_live_bar(symbol)
        if live_bar is not None and not live_bar.empty:
            logging.info(f"Synthesizing {today_str} daily bar from Live Snapshot for {symbol}.")
            if df is None or df.empty:
                df = live_bar
            else:
                df = pd.concat([df, live_bar], ignore_index=True)
    # -------------------------------------------------------------
    
    if df is None or df.empty:
        logging.warning(f"No daily data found for {symbol}.")
        return

    # Keep only date part
    df['date'] = df['datetime'].dt.strftime('%Y-%m-%d')
    df = df.drop(columns=['datetime'])
    
    # Filter to requested start date for storage
    df_store = df[df['date'] >= start_date].copy()

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"DELETE FROM daily_bars WHERE symbol='{symbol}' AND date >= '{start_date}'")
    df_store.to_sql('daily_bars', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    logging.info(f"Stored {len(df_store)} daily SIP records for {symbol}.")

def fetch_and_store_monthly(symbol, start_date="2023-01-01", end_date="2025-12-31"):
    logging.info(f"Fetching Monthly SIP data for {symbol}...")
    
    actual_start = "2015-01-01"
    
    df = _fetch_alpaca_bars(symbol, TimeFrame(1, TimeFrameUnit.Month), actual_start, end_date)
    
    if df is None or df.empty:
        logging.warning(f"No monthly data found for {symbol}.")
        return

    # Keep only date part structured as YYYY-MM-01
    df['date'] = df['datetime'].dt.strftime('%Y-%m-01')
    df = df.drop(columns=['datetime'])
    
    # Filter to proper start date
    df_store = df[df['date'] >= start_date].copy()

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"DELETE FROM monthly_bars WHERE symbol='{symbol}' AND date >= '{start_date}'")
    df_store.to_sql('monthly_bars', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    logging.info(f"Stored {len(df_store)} monthly SIP records for {symbol}.")

def fetch_and_store_intraday(symbol, start_date="2024-01-01", end_date="2025-12-31"):
    """Fetches historical 15m data point-in-time from Alpaca SIP."""
    logging.info(f"Fetching 15m Intraday SIP data for {symbol}...")
    
    df = _fetch_alpaca_bars(symbol, TimeFrame(15, TimeFrameUnit.Minute), start_date, end_date)
    if df is None or df.empty:
        logging.warning(f"No 15m intraday data found for {symbol} on Alpaca.")
        return
        
    df['datetime'] = df['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"DELETE FROM intraday_15m_bars WHERE symbol='{symbol}'")
    df.to_sql('intraday_15m_bars', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    logging.info(f"Stored {len(df)} intraday 15m SIP records for {symbol}.")

def fetch_and_store_intraday_1h(symbol, start_date="2024-01-01", end_date="2025-12-31"):
    """Fetches historical 1-hour data point-in-time from Alpaca SIP."""
    logging.info(f"Fetching 1h Intraday SIP data for {symbol}...")
    
    df = _fetch_alpaca_bars(symbol, TimeFrame(1, TimeFrameUnit.Hour), start_date, end_date)
    if df is None or df.empty:
        logging.warning(f"No 1h intraday data found for {symbol} on Alpaca.")
        return
        
    df['datetime'] = df['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"DELETE FROM intraday_1h_bars WHERE symbol='{symbol}'")
    df.to_sql('intraday_1h_bars', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    logging.info(f"Stored {len(df)} intraday 1h SIP records for {symbol}.")


if __name__ == "__main__":
    init_db()
    
    test_symbols = ["SPY", "NVDA"]
    today = datetime.now().strftime('%Y-%m-%d')
    start_test = (datetime.now() - pd.DateOffset(months=6)).strftime('%Y-%m-%d')
    
    print("--- Testing Alpaca SIP Premium Connection ---")
    for sym in test_symbols:
        try:
            fetch_and_store_daily(sym, start_date=start_test, end_date=today)
            fetch_and_store_monthly(sym, start_date="2024-01-01", end_date=today)
        except Exception as e:
            logging.error(f"Failed processing {sym}: {e}")
            
    # Verify in DB
    conn = get_db_connection()
    df_verify = pd.read_sql("SELECT symbol, date, close FROM daily_bars WHERE symbol='NVDA' ORDER BY date DESC LIMIT 3", conn)
    print("\n[VERIFICATION] Last 3 Daily NVDA Closes from SIP:")
    print(df_verify)
    conn.close()
    logging.info("Dry run complete.")
