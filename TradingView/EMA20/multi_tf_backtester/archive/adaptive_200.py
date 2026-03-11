"""
Adaptive 200-Day Momentum Strategy (1x Native Assets)
=====================================================
Goal: Beat 5-Year Buy & Hold on 1x Native symbols without leverage.
- Regime Filter: Daily Close > Daily 200 SMA (Bull Market = Long, Bear Market = 100% Cash)
- Entry: RSI(14) crosses above oversold (e.g. 40) OR MACD Histogram > 0
- Exit 1: Drop below Daily 50 SMA (Trend broken)
- Exit 2: Wide ATR Trailing Stop (e.g. 4.0 - 5.0) to survive normal pullbacks
- Exit 3: Price drops below 200 SMA (Crash protection - Absolute exit)
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

def compute_sma(series, period):
    return series.rolling(window=period).mean()

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

def run_adaptive_backtest(symbol, initial_capital=10000,
                          macd_fast=12, macd_slow=26, macd_sig=9,
                          rsi_oversold=40, atr_trail_mult=4.0):
    """
    Backtester for the Adaptive 200-Day SMA engine using Daily bars.
    """
    conn = get_db_connection()
    daily = pd.read_sql(f"SELECT * FROM daily_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    conn.close()
    
    if daily.empty:
        return None
        
    daily['date'] = pd.to_datetime(daily['date'])
    
    # 1. Regime & Trend Filters
    daily['sma_200'] = compute_sma(daily['close'], 200)
    daily['sma_50'] = compute_sma(daily['close'], 50)
    
    # 2. Momentum Indicators
    daily['macd'], daily['macd_sig'], daily['macd_hist'] = compute_macd(daily['close'], macd_fast, macd_slow, macd_sig)
    daily['rsi'] = compute_rsi(daily['close'], 14)
    daily['atr'] = compute_atr(daily, 14)
    
    # 3. Simulation
    capital = initial_capital
    in_position = False
    entry_price = 0.0
    entry_date = None
    shares = 0
    highest_close = 0.0
    trailing_stop = 0.0
    
    trades = []
    equity = []
    
    # Start loop after moving averages populate
    start_idx = 200 
    if start_idx >= len(daily): return None
    
    for i in range(start_idx, len(daily)):
        today = daily.iloc[i]
        yesterday = daily.iloc[i - 1]
        
        port_val = capital + (shares * today['close'] if in_position else 0)
        equity.append({'date': today['date'], 'equity': port_val})
        
        if in_position:
            # Update Trailing Stop
            if today['close'] > highest_close:
                highest_close = today['close']
                atr_val = today['atr'] if not pd.isna(today['atr']) else highest_close * 0.02
                trailing_stop = highest_close - (atr_val * atr_trail_mult)
            
            exit_triggered = False
            exit_reason = ""
            
            # Exit 1: Massive Crash Protection (Drop below 200 SMA)
            if today['close'] < today['sma_200']:
                exit_triggered = True
                exit_reason = "200 SMA Trend Break"
                
            # Exit 2: Short-term Trend Break (Drop below 50 SMA)
            elif today['close'] < today['sma_50']:
                exit_triggered = True
                exit_reason = "50 SMA Trend Break"

            # Exit 3: Wide ATR Trailing Stop
            elif today['close'] < trailing_stop:
                exit_triggered = True
                exit_reason = "Trailing Stop"
                
            if exit_triggered:
                exit_price = today['close']
                exit_date = today['date']
                revenue = shares * exit_price
                capital += revenue
                pnl = revenue - (shares * entry_price)
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                hold_days = (exit_date - entry_date).days
                
                trades.append({
                    'symbol': symbol,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_date': exit_date, 'exit_price': exit_price,
                    'shares': shares, 'pnl': pnl, 'pnl_pct': pnl_pct,
                    'hold_days': hold_days, 'exit_reason': exit_reason,
                    'capital_after': capital
                })
                in_position = False
                shares = 0
                highest_close = 0.0
                
        elif not in_position and i >= max(200, 2):
            day_before = daily.iloc[i - 2]
            
            # Regime Filter: We MUST be in a confirmed Bull Market (> 200 SMA)
            bull_regime = yesterday['close'] > yesterday['sma_200']
            
            if bull_regime:
                # Entry 1: MACD Histogram crosses positive (momentum rising)
                macd_cross_up = (yesterday['macd_hist'] > 0 and day_before['macd_hist'] <= 0)
                
                # Entry 2: RSI hooks up from oversold
                rsi_hook_up = (yesterday['rsi'] > rsi_oversold and day_before['rsi'] <= rsi_oversold)
                
                # Entry 3: Price crosses BACK ABOVE 50 SMA while > 200 SMA (Trend resumption)
                sma50_cross_up = (yesterday['close'] > yesterday['sma_50'] and day_before['close'] <= day_before['sma_50'])
                
                if macd_cross_up or rsi_hook_up or sma50_cross_up:
                    entry_price = today['open']
                    shares = int(capital // entry_price)
                    
                    if shares > 0:
                        capital -= shares * entry_price
                        in_position = True
                        entry_date = today['date']
                        highest_close = entry_price
                        atr_val = yesterday['atr'] if not pd.isna(yesterday['atr']) else entry_price * 0.02
                        trailing_stop = highest_close - (atr_val * atr_trail_mult)
    
    # Close open position at end
    if in_position:
        last = daily.iloc[-1]
        revenue = shares * last['close']
        capital += revenue
        pnl = revenue - (shares * entry_price)
        trades.append({
            'symbol': symbol,
            'entry_date': entry_date, 'entry_price': entry_price,
            'exit_date': last['date'], 'exit_price': last['close'],
            'shares': shares, 'pnl': pnl,
            'pnl_pct': (last['close'] - entry_price) / entry_price * 100,
            'hold_days': (last['date'] - entry_date).days,
            'exit_reason': 'End of data', 'capital_after': capital
        })
        
    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity)
    
    # Calculate Benchmarks using the SAME time window (start_idx to end)
    sim_start_date = daily.iloc[start_idx]['date']
    daily_sim_window = daily[daily['date'] >= sim_start_date]
    if daily_sim_window.empty: return None
    
    first_price = daily_sim_window.iloc[0]['close']
    last_price = daily_sim_window.iloc[-1]['close']
    bh_return = (last_price - first_price) / first_price * 100
    bh_shares = int(initial_capital // first_price)
    
    strategy_return = (capital - initial_capital) / initial_capital * 100
    
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['dd'] = (equity_df['peak'] - equity_df['equity']) / equity_df['peak'] * 100
        max_dd = equity_df['dd'].max()
    else:
        max_dd = 0
        
    bh_equity = daily_sim_window['close'] * bh_shares + (initial_capital - bh_shares * first_price)
    bh_dd = ((bh_equity.cummax() - bh_equity) / bh_equity.cummax() * 100).max()
    
    total_hold = trades_df['hold_days'].sum() if not trades_df.empty else 0
    total_days = (daily_sim_window.iloc[-1]['date'] - daily_sim_window.iloc[0]['date']).days
    pct_in = total_hold / total_days * 100 if total_days > 0 else 0
    
    return {
        'symbol': symbol,
        'strategy_return': strategy_return, 'bh_return': bh_return,
        'excess': strategy_return - bh_return,
        'max_dd': max_dd, 'bh_dd': bh_dd,
        'num_trades': len(trades_df),
        'win_rate': len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100 if not trades_df.empty else 0,
        'avg_hold': trades_df['hold_days'].mean() if not trades_df.empty else 0,
        'pct_in_market': pct_in,
        'trades': trades_df,
        'params': {'macd': f"{macd_fast}/{macd_slow}/{macd_sig}", 'rsi_os': rsi_oversold, 'atr_trail': atr_trail_mult}
    }

if __name__ == "__main__":
    symbols = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA']
    
    rsis = [40, 45, 50]
    atrs = [3.0, 4.0, 5.0, 6.0]
    
    for s in symbols:
        print(f"\nEvaluating Adaptive 200-SMA on {s} (2021-2026)")
        best_ret = -999
        best_res = None
        
        for r, a in itertools.product(rsis, atrs):
            res = run_adaptive_backtest(s, rsi_oversold=r, atr_trail_mult=a)
            if res and res['num_trades'] > 2:
                if res['strategy_return'] > best_ret:
                    best_ret = res['strategy_return']
                    best_res = res
                    
        if best_res:
            r = best_res
            print(f"[{s} Strategy vs Buy&Hold]")
            print(f"  Return: {r['strategy_return']:>+7.1f}% | B&H: {r['bh_return']:>+7.1f}%")
            print(f"  Max DD: {r['max_dd']:>6.1f}%  | B&H DD: {r['bh_dd']:>6.1f}%")
            print(f"  Trades: {r['num_trades']} (Win Rate: {r['win_rate']:.0f}%, Avg Hold: {r['avg_hold']:.0f}d)")
            print(f"  Time in Market: {r['pct_in_market']:.0f}%")
            print(f"  Params: RSI {r['params']['rsi_os']}, ATR {r['params']['atr_trail']}")
            print(f"  Verdict: {'✅ BEATS B&H' if r['strategy_return'] > r['bh_return'] else '❌ LOSES TO B&H'}")
