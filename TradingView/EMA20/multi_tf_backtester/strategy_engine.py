import sqlite3
import pandas as pd
import numpy as np
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def compute_atr(df, period=14):
    """Calculates Average True Range."""
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    df['atr'] = true_range.rolling(period).mean()
    return df

def compute_ema(df, period=20, column='close'):
    """Calculates EMA exactly as EMA20 needs it."""
    df[f'ema_{period}'] = df[column].ewm(span=period, adjust=False).mean()
    return df

def generate_signals(symbol, lookback_months_for_slope=3, min_pullback_bars=3, 
                     min_slope_pct=0.005, timeframe='1h', require_daily_trend=True):
    """
    Redesigned 3-tier strategy:
    1. Monthly EMA20 must be pointing Up with meaningful slope (> min_slope_pct of price).
    2. Daily close must be > Daily EMA20 (prior day — intermediate trend confirmation).
    3. On the trigger timeframe (1h or 15m), price must have been BELOW the EMA
       for at least min_pullback_bars consecutive bars (a real pullback).
    4. Triggers LONG when trigger-TF candle crosses back above its EMA20 after pullback.
    """
    conn = get_db_connection()
    
    # 1. Load and process Monthly
    monthly_df = pd.read_sql(f"SELECT * FROM monthly_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    if monthly_df.empty:
        logging.warning(f"No monthly data for {symbol} to run strategy.")
        conn.close()
        return None
        
    monthly_df = compute_ema(monthly_df, period=20)
    monthly_df['date'] = pd.to_datetime(monthly_df['date'])
    
    # Calculate Monthly Slope (difference over N months)
    monthly_df['ema_slope'] = monthly_df['ema_20'] - monthly_df['ema_20'].shift(lookback_months_for_slope)
    
    # --- FIX 3: Strengthened monthly filter ---
    # Require slope to be a meaningful % of price, not just barely positive
    monthly_df['slope_pct'] = monthly_df['ema_slope'] / monthly_df['close']
    monthly_df['is_bull_regime'] = (
        (monthly_df['close'] > monthly_df['ema_20']) & 
        (monthly_df['slope_pct'] > min_slope_pct)
    )
    
    # 2. Load and process Daily bars for intermediate trend confirmation
    daily_df = pd.read_sql(f"SELECT * FROM daily_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    daily_trend_available = False
    if not daily_df.empty and require_daily_trend:
        daily_df = compute_ema(daily_df, period=20)
        daily_df['date'] = pd.to_datetime(daily_df['date'])
        # Shift daily by 1 day to prevent look-ahead bias
        # (we only know yesterday's close at today's open)
        daily_df['daily_trend_up'] = (daily_df['close'] > daily_df['ema_20']).shift(1)
        daily_df['daily_date_key'] = daily_df['date'].dt.strftime('%Y-%m-%d')
        daily_trend_available = True
    
    # 3. Load trigger timeframe bars (1h or 15m)
    if timeframe == '1h':
        table_name = 'intraday_1h_bars'
    else:
        table_name = 'intraday_15m_bars'
        
    intraday_df = pd.read_sql(f"SELECT * FROM {table_name} WHERE symbol='{symbol}' ORDER BY datetime", conn)
    if intraday_df.empty:
        logging.warning(f"No {timeframe} data for {symbol} to run strategy.")
        conn.close()
        return None
        
    intraday_df = compute_ema(intraday_df, period=20)
    intraday_df = compute_atr(intraday_df, period=14)
    intraday_df['datetime'] = pd.to_datetime(intraday_df['datetime'])
    intraday_df['monthly_date_key'] = intraday_df['datetime'].dt.to_period('M').dt.to_timestamp()
    
    # Shift Monthly data by 1 month to prevent look-ahead bias
    monthly_regime = monthly_df[['date', 'is_bull_regime', 'ema_20', 'ema_slope', 'slope_pct']].copy()
    monthly_regime['date'] = monthly_regime['date'] + pd.DateOffset(months=1)
    monthly_regime['date'] = monthly_regime['date'].dt.to_period('M').dt.to_timestamp()
    monthly_regime.rename(columns={'date': 'monthly_date_key', 'ema_20': 'monthly_ema_20'}, inplace=True)
    
    # Merge Monthly regime onto trigger-TF data
    merged_df = pd.merge(intraday_df, monthly_regime, on='monthly_date_key', how='left')
    
    # Merge Daily trend confirmation
    if daily_trend_available:
        merged_df['daily_date_key'] = merged_df['datetime'].dt.strftime('%Y-%m-%d')
        daily_trend_map = daily_df[['daily_date_key', 'daily_trend_up']].drop_duplicates()
        merged_df = pd.merge(merged_df, daily_trend_map, on='daily_date_key', how='left')
        merged_df['daily_trend_up'] = merged_df['daily_trend_up'].fillna(False)
    else:
        merged_df['daily_trend_up'] = True  # Skip daily filter if not available
    
    # --- FIX 2: Track consecutive bars below EMA for pullback detection ---
    merged_df['below_ema'] = merged_df['close'] < merged_df['ema_20']
    
    # Calculate consecutive bars below EMA
    # When below_ema is True, increment counter; when False, reset to 0
    bars_below = []
    count = 0
    for val in merged_df['below_ema']:
        if val:
            count += 1
        else:
            count = 0
        bars_below.append(count)
    merged_df['bars_below_ema'] = bars_below
    
    # Track the max consecutive bars below EMA BEFORE the current bar
    # (to know at the moment of cross-up, how long was the pullback)
    merged_df['prev_bars_below'] = merged_df['bars_below_ema'].shift(1).fillna(0)
    
    # Cross condition
    merged_df['prev_close'] = merged_df['close'].shift(1)
    merged_df['prev_ema_20'] = merged_df['ema_20'].shift(1)
    
    # Cross up: previous close was below EMA, current close is above
    merged_df['ema_cross_up'] = (merged_df['prev_close'] <= merged_df['prev_ema_20']) & (merged_df['close'] > merged_df['ema_20'])
    
    # The actual Buy Signal (3-tier filter):
    # 1. Monthly regime is bullish (macro trend)
    # 2. Daily close > Daily EMA20 (intermediate trend confirmation)
    # 3. Trigger-TF just crossed above its EMA20 after a real pullback
    merged_df['is_bull_regime'] = merged_df['is_bull_regime'].fillna(False)
    merged_df['buy_signal'] = (
        merged_df['is_bull_regime'] & 
        merged_df['daily_trend_up'] &
        merged_df['ema_cross_up'] & 
        (merged_df['prev_bars_below'] >= min_pullback_bars)
    )
    merged_df['buy_signal'] = merged_df['buy_signal'].fillna(False)
    
    conn.close()
    
    total_signals = merged_df['buy_signal'].sum()
    logging.info(f"Generated signals for {symbol} ({timeframe}). Total bars: {len(merged_df)}, Buy signals: {total_signals}")
    return merged_df

if __name__ == "__main__":
    test_symbol = "SPY"
    df = generate_signals(test_symbol, timeframe='1h')
    if df is not None:
        print(f"\nSample of Buy Signals for {test_symbol}:")
        buys = df[df['buy_signal'] == True]
        if not buys.empty:
            print(buys[['datetime', 'close', 'ema_20', 'monthly_ema_20', 'ema_slope', 'prev_bars_below']].head(10))
            print(f"\nTotal buy signals: {len(buys)}")
        else:
            print("No buy signals found in the available timeframe.")
