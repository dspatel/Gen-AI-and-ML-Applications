"""
Bidirectional Alpha Engine (Long / Short)
=========================================
Goal: Survive and thrive in BOTH Bull and Bear markets.
- In Bull Regime: Long Native or Long Leveraged ETF (e.g. QLD)
- In Bear Regime: Long Inverse ETF (e.g. SQQQ or PSQ) to short the market.
"""
import sqlite3
import pandas as pd
import numpy as np
import os
import itertools
import logging

logging.basicConfig(level=logging.WARNING, format='%(message)s')
DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_macd(close, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd = ema_fast - ema_slow
    signal_line = compute_ema(macd, signal)
    histogram = macd - signal_line
    return macd, signal_line, histogram

def compute_rsi(close, period=14):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()

def run_bidirectional_backtest(native_sym, long_sym, short_sym, initial_capital=10000,
                               macd_fast=12, macd_slow=26, macd_sig=9,
                               rsi_oversold=40, rsi_overbought=60, atr_trail_mult=3.0,
                               daily_ema_filter=50, monthly_slope_min=0.005):
    """
    Simulates buying 'long_sym' when native market is Bullish,
    and buying 'short_sym' (inverse ETF) when native market is Bearish.
    Entry logic runs on the NATIVE asset signals.
    Execution happens on the LONG/SHORT derivatives.
    """
    conn = get_db_connection()
    
    # 1. Monthly Regime (Based on NATIVE)
    monthly = pd.read_sql(f"SELECT * FROM monthly_bars WHERE symbol='{native_sym}' ORDER BY date", conn)
    if monthly.empty:
        conn.close()
        return None
    
    monthly['date'] = pd.to_datetime(monthly['date'])
    monthly['ema_20'] = compute_ema(monthly['close'], 20)
    monthly['ema_slope'] = monthly['ema_20'] - monthly['ema_20'].shift(3)
    monthly['slope_pct'] = monthly['ema_slope'] / monthly['close']
    monthly['month_bull'] = (monthly['close'] > monthly['ema_20']) & (monthly['slope_pct'] > monthly_slope_min)
    
    regime = monthly[['date', 'month_bull']].copy()
    regime['date'] = regime['date'] + pd.DateOffset(months=1)
    regime['month_key'] = regime['date'].dt.to_period('M')
    
    # 2. Daily Signals (Based on NATIVE)
    daily_nat = pd.read_sql(f"SELECT * FROM daily_bars WHERE symbol='{native_sym}' ORDER BY date", conn)
    if daily_nat.empty: return None
    daily_nat['date'] = pd.to_datetime(daily_nat['date'])
    daily_nat['ema_filter'] = compute_ema(daily_nat['close'], daily_ema_filter)
    daily_nat['macd'], daily_nat['macd_sig'], daily_nat['macd_hist'] = compute_macd(daily_nat['close'], macd_fast, macd_slow, macd_sig)
    daily_nat['rsi'] = compute_rsi(daily_nat['close'], 14)
    daily_nat['month_key'] = daily_nat['date'].dt.to_period('M')
    daily_nat = pd.merge(daily_nat, regime[['month_key', 'month_bull']], on='month_key', how='left')
    daily_nat['month_bull'] = daily_nat['month_bull'].fillna(False)
    
    # 3. Tradable Assets Data (Long ETF and Short ETF)
    daily_long = pd.read_sql(f"SELECT date, open, high, low, close FROM daily_bars WHERE symbol='{long_sym}' ORDER BY date", conn)
    daily_short = pd.read_sql(f"SELECT date, open, high, low, close FROM daily_bars WHERE symbol='{short_sym}' ORDER BY date", conn)
    conn.close()
    
    if daily_long.empty or daily_short.empty: return None
    
    daily_long['date'] = pd.to_datetime(daily_long['date'])
    daily_long['atr'] = compute_atr(daily_long, 14)
    
    daily_short['date'] = pd.to_datetime(daily_short['date'])
    daily_short['atr'] = compute_atr(daily_short, 14)
    
    # Merge all needed execution data into the Native dataframe for easy row iteration
    long_cols = daily_long[['date', 'open', 'close', 'atr']].copy()
    long_cols.columns = ['date', 'open_long', 'close_long', 'atr_long']
    short_cols = daily_short[['date', 'open', 'close', 'atr']].copy()
    short_cols.columns = ['date', 'open_short', 'close_short', 'atr_short']
    
    daily = pd.merge(daily_nat, long_cols, on='date', how='inner')
    daily = pd.merge(daily, short_cols, on='date', how='inner')
    
    # 4. Simulation
    capital = initial_capital
    in_pos_type = None # 'LONG' or 'SHORT'
    entry_price = 0.0
    entry_date = None
    shares = 0
    highest_close = 0.0
    trailing_stop = 0.0
    trades = []
    equity = []
    
    for i in range(1, len(daily)):
        today = daily.iloc[i]
        yesterday = daily.iloc[i - 1]
        
        # Current portfolio value based on active asset
        if in_pos_type == 'LONG':
            port_val = capital + (shares * today['close_long'])
        elif in_pos_type == 'SHORT':
            port_val = capital + (shares * today['close_short'])
        else:
            port_val = capital
            
        equity.append({'date': today['date'], 'equity': port_val})
        
        # Active Position Management
        if in_pos_type == 'LONG':
            # Update Trailing Stop (Based on Long ETF Price)
            if today['close_long'] > highest_close:
                highest_close = today['close_long']
                atr_val = today['atr_long'] if not pd.isna(today['atr_long']) else highest_close * 0.02
                trailing_stop = highest_close - (atr_val * atr_trail_mult)
            
            exit_triggered = False
            # Exit 1: Trailing stop hit on the traded asset
            if today['close_long'] < trailing_stop:
                exit_triggered, exit_reason = True, "Trailing Stop"
            # Exit 2: MACD Exhaustion on Native Asset
            elif today['close_long'] > entry_price and yesterday['macd_hist'] > 0 and today['macd_hist'] < 0:
                exit_triggered, exit_reason = True, "MACD Down (Profit)"
            # Exit 3: Regime Flip on Native
            elif not today['month_bull'] and yesterday['month_bull']:
                exit_triggered, exit_reason = True, "Regime Flip Bear"
                
            if exit_triggered:
                revenue = shares * today['close_long']
                capital += revenue
                pnl = revenue - (shares * entry_price)
                trades.append({
                    'type': 'LONG', 'symbol': long_sym,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_date': today['date'], 'exit_price': today['close_long'],
                    'pnl': pnl, 'pnl_pct': (today['close_long'] - entry_price)/entry_price * 100,
                    'capital_after': capital, 'reason': exit_reason
                })
                in_pos_type = None; shares = 0
                
        elif in_pos_type == 'SHORT':
            # Update Trailing Stop (Based on Short ETF Price - remember we are LONG the Short ETF!)
            if today['close_short'] > highest_close:
                highest_close = today['close_short']
                atr_val = today['atr_short'] if not pd.isna(today['atr_short']) else highest_close * 0.02
                trailing_stop = highest_close - (atr_val * atr_trail_mult)
            
            exit_triggered = False
            # Exit 1: Trailing stop on Inverse ETF hit
            if today['close_short'] < trailing_stop:
                exit_triggered, exit_reason = True, "Trailing Stop"
            # Exit 2: MACD Exhaustion on Native (Short side: MACD histogram crosses UP)
            elif today['close_short'] > entry_price and yesterday['macd_hist'] < 0 and today['macd_hist'] > 0:
                exit_triggered, exit_reason = True, "MACD Up (Profit)"
            # Exit 3: Regime Flip Native (Bear -> Bull)
            elif today['month_bull'] and not yesterday['month_bull']:
                exit_triggered, exit_reason = True, "Regime Flip Bull"
                
            if exit_triggered:
                revenue = shares * today['close_short']
                capital += revenue
                pnl = revenue - (shares * entry_price)
                trades.append({
                    'type': 'SHORT', 'symbol': short_sym,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_date': today['date'], 'exit_price': today['close_short'],
                    'pnl': pnl, 'pnl_pct': (today['close_short'] - entry_price)/entry_price * 100,
                    'capital_after': capital, 'reason': exit_reason
                })
                in_pos_type = None; shares = 0
                
        # Entry seeking if Flat
        if in_pos_type is None and i >= 2:
            day_before = daily.iloc[i - 2]
            
            # --- LONG LOGIC ---
            bull_regime = yesterday['month_bull'] and yesterday['close'] > yesterday['ema_filter']
            if bull_regime:
                macd_cross_up = (yesterday['macd_hist'] > 0 and day_before['macd_hist'] <= 0)
                rsi_hook_up = (yesterday['rsi'] > rsi_oversold and day_before['rsi'] <= rsi_oversold)
                if macd_cross_up or rsi_hook_up:
                    entry_price = today['open_long']
                    shares = int(capital // entry_price)
                    if shares > 0:
                        capital -= shares * entry_price
                        in_pos_type = 'LONG'
                        entry_date = today['date']
                        highest_close = entry_price
                        atr_val = yesterday['atr_long'] if not pd.isna(yesterday['atr_long']) else entry_price*0.02
                        trailing_stop = highest_close - (atr_val * atr_trail_mult)
                        
            # --- SHORT LOGIC ---
            else: # Bear Regime
                bear_regime = not yesterday['month_bull'] and yesterday['close'] < yesterday['ema_filter']
                if bear_regime:
                    # Inverse entries: MACD crosses DOWN, RSI hooks DOWN from overbought
                    macd_cross_down = (yesterday['macd_hist'] < 0 and day_before['macd_hist'] >= 0)
                    rsi_hook_down = (yesterday['rsi'] < rsi_overbought and day_before['rsi'] >= rsi_overbought)
                    if macd_cross_down or rsi_hook_down:
                        # Buy the Inverse ETF
                        entry_price = today['open_short']
                        shares = int(capital // entry_price)
                        if shares > 0:
                            capital -= shares * entry_price
                            in_pos_type = 'SHORT'
                            entry_date = today['date']
                            highest_close = entry_price
                            atr_val = yesterday['atr_short'] if not pd.isna(yesterday['atr_short']) else entry_price*0.02
                            trailing_stop = highest_close - (atr_val * atr_trail_mult)

    # Force Close
    if in_pos_type == 'LONG':
        last = daily.iloc[-1]
        revenue = shares * last['close_long']
        capital += revenue
        trades.append({
            'type': 'LONG', 'symbol': long_sym, 'entry_date': entry_date, 'entry_price': entry_price,
            'exit_date': last['date'], 'exit_price': last['close_long'], 'pnl': revenue-(shares*entry_price),
            'capital_after': capital, 'reason': 'End of Data'
        })
    elif in_pos_type == 'SHORT':
        last = daily.iloc[-1]
        revenue = shares * last['close_short']
        capital += revenue
        trades.append({
            'type': 'SHORT', 'symbol': short_sym, 'entry_date': entry_date, 'entry_price': entry_price,
            'exit_date': last['date'], 'exit_price': last['close_short'], 'pnl': revenue-(shares*entry_price),
            'capital_after': capital, 'reason': 'End of Data'
        })

    # Stats
    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity)
    
    first_price_nat = daily.iloc[0]['close']
    last_price_nat = daily.iloc[-1]['close']
    nat_return = (last_price_nat - first_price_nat) / first_price_nat * 100
    
    strategy_return = (capital - initial_capital) / initial_capital * 100
    
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['dd'] = (equity_df['peak'] - equity_df['equity']) / equity_df['peak'] * 100
        max_dd = equity_df['dd'].max()
    else:
        max_dd = 0
        
    bh_shares = int(initial_capital // first_price_nat)
    bh_equity = daily['close'] * bh_shares + (initial_capital - bh_shares * first_price_nat)
    bh_dd = ((bh_equity.cummax() - bh_equity) / bh_equity.cummax() * 100).max()
    
    return {
        'strategy_return': strategy_return, 'max_dd': max_dd,
        'bh_return': nat_return, 'bh_dd': bh_dd,
        'num_trades': len(trades_df),
        'num_longs': len(trades_df[trades_df['type']=='LONG']) if not trades_df.empty else 0,
        'num_shorts': len(trades_df[trades_df['type']=='SHORT']) if not trades_df.empty else 0,
        'win_rate': len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100 if not trades_df.empty else 0,
        'trades': trades_df,
        'equity': equity_df
    }
