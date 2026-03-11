import os
import sqlite3
import pandas as pd
import yfinance as yf
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')

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

def fetch_and_store_daily(symbol, start_date="2023-01-01", end_date="2025-12-31"):
    logging.info(f"Fetching Daily data for {symbol}...")
    # Fetch extra history for EMA calc
    df = yf.download(symbol, start="2022-01-01", end=end_date, interval="1d", auto_adjust=False, progress=False)
    
    if df.empty:
        logging.warning(f"No daily data found for {symbol}.")
        return

    # Flatten multi-index if yfinance returned it
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
        
    df = df.reset_index()
    # rename columns to lowercase
    df.rename(columns={
        'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 
        'Close': 'close', 'Volume': 'volume'
    }, inplace=True)
    
    # Keep only date part
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')
    
    # Filter to start date
    df = df[df['date'] >= start_date].copy()

    records = df[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']].copy() if 'symbol' in df.columns else df.copy()
    records['symbol'] = symbol

    conn = get_db_connection()
    records[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']].to_sql(
        'daily_bars', conn, if_exists='append', index=False
    )
    conn.commit()
    conn.close()
    logging.info(f"Stored {len(records)} daily records for {symbol}.")

def fetch_and_store_monthly(symbol, start_date="2023-01-01", end_date="2025-12-31"):
    logging.info(f"Fetching Monthly data for {symbol}...")
    df = yf.download(symbol, start="2015-01-01", end=end_date, interval="1mo", auto_adjust=False, progress=False)
    
    if df.empty:
        logging.warning(f"No monthly data found for {symbol}.")
        return

    # Flatten multi-index if yfinance returned it
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    df = df.reset_index()
    df.rename(columns={
        'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 
        'Close': 'close', 'Volume': 'volume'
    }, inplace=True)
    
    # Keep only date part (Year-Month-01)
    df['date'] = df['date'].dt.strftime('%Y-%m-01')
    
    # Filter to start date
    df = df[df['date'] >= start_date].copy()

    records = df[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']].copy() if 'symbol' in df.columns else df.copy()
    records['symbol'] = symbol

    conn = get_db_connection()
    records[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']].to_sql(
        'monthly_bars', conn, if_exists='append', index=False
    )
    conn.commit()
    conn.close()
    logging.info(f"Stored {len(records)} monthly records for {symbol}.")


def _get_alpaca_client():
    """Creates an Alpaca StockHistoricalDataClient using environment variables."""
    from alpaca.data.historical import StockHistoricalDataClient
    
    api_key = os.environ.get('R6_ALPACA_API_KEY')
    api_secret = os.environ.get('R6_ALPACA_SECRET_KEY')
    
    if not api_key or not api_secret:
        logging.error("R6_ALPACA_API_KEY and R6_ALPACA_SECRET_KEY must be set in the environment.")
        return None
        
    return StockHistoricalDataClient(api_key, api_secret)


def _fetch_alpaca_bars(symbol, timeframe_obj, start_date, end_date):
    """Generic Alpaca bar fetcher using alpaca-py SDK."""
    from alpaca.data.requests import StockBarsRequest
    
    client = _get_alpaca_client()
    if client is None:
        return None
    
    try:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe_obj,
            start=datetime.strptime(start_date, '%Y-%m-%d'),
            end=datetime.strptime(end_date, '%Y-%m-%d'),
            adjustment='raw'
        )
        barset = client.get_stock_bars(request)
        bars_df = barset.df
    except Exception as e:
        logging.error(f"Error fetching data from Alpaca for {symbol}: {e}")
        return None
    
    if bars_df.empty:
        return None
    
    bars_df = bars_df.reset_index()
    time_col = 'timestamp' if 'timestamp' in bars_df.columns else 'time'
    
    result = pd.DataFrame({
        'symbol': symbol,
        'datetime': bars_df[time_col].dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d %H:%M:%S'),
        'open': bars_df['open'],
        'high': bars_df['high'],
        'low': bars_df['low'],
        'close': bars_df['close'],
        'volume': bars_df['volume']
    })
    return result


def fetch_and_store_intraday(symbol, start_date="2024-01-01", end_date="2025-12-31"):
    """Fetches historical 15m data using Alpaca API (alpaca-py)."""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    
    logging.info(f"Fetching 15m Intraday data for {symbol} using Alpaca...")
    
    df = _fetch_alpaca_bars(symbol, TimeFrame(15, TimeFrameUnit.Minute), start_date, end_date)
    if df is None or df.empty:
        logging.warning(f"No 15m intraday data found for {symbol} on Alpaca.")
        return

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"DELETE FROM intraday_15m_bars WHERE symbol='{symbol}'")
    df.to_sql('intraday_15m_bars', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    logging.info(f"Stored {len(df)} intraday 15m records for {symbol} from Alpaca.")


def fetch_and_store_intraday_1h(symbol, start_date="2024-01-01", end_date="2025-12-31"):
    """Fetches historical 1-hour data using Alpaca API (alpaca-py)."""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    
    logging.info(f"Fetching 1h Intraday data for {symbol} using Alpaca...")
    
    df = _fetch_alpaca_bars(symbol, TimeFrame(1, TimeFrameUnit.Hour), start_date, end_date)
    if df is None or df.empty:
        logging.warning(f"No 1h intraday data found for {symbol} on Alpaca.")
        return

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"DELETE FROM intraday_1h_bars WHERE symbol='{symbol}'")
    df.to_sql('intraday_1h_bars', conn, if_exists='append', index=False)
    conn.commit()
    conn.close()
    
    logging.info(f"Stored {len(df)} intraday 1h records for {symbol} from Alpaca.")


if __name__ == "__main__":
    init_db()
    
    test_symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    
    for sym in test_symbols:
        try:
            fetch_and_store_monthly(sym, start_date="2024-01-01", end_date="2025-12-31")
            fetch_and_store_daily(sym, start_date="2024-01-01", end_date="2025-12-31")
            fetch_and_store_intraday(sym)
            fetch_and_store_intraday_1h(sym)
        except Exception as e:
            logging.error(f"Failed processing {sym}: {e}")
            
    logging.info("Data fetching complete.")
