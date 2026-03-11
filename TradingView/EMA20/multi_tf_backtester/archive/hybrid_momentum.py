"""
Hybrid Momentum Regime Strategy
===============================
Goal: Beat Buy & Hold by eliminating Moving Average entry lag.
- Regime Filter: Monthly EMA20 and Daily EMA50 must be bullish (Price > EMA).
- Entry: MACD Histogram crosses above 0 (momentum shifting up) OR RSI dips below 40 and hooks up.
- Exit: ATR Trailing Stop OR MACD Histogram crosses below 0 while in profit OR Regime flips bearish.
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
    """Returns MACD line, Signal line, and Histogram."""
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

def run_hybrid_backtest(symbol, initial_capital=10000,
                        macd_fast=12, macd_slow=26, macd_sig=9,
                        rsi_oversold=40, atr_trail_mult=2.5,
                        daily_ema_filter=50, monthly_slope_min=0.005):
    """
    Hybrid Momentum Backtester
    """
    conn = get_db_connection()
    
    # --- 1. Monthly Regime ---
    monthly = pd.read_sql(f"SELECT * FROM monthly_bars WHERE symbol='{symbol}' ORDER BY date", conn)
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
    
    # --- 2. Daily Data & Indicators ---
    daily = pd.read_sql(f"SELECT * FROM daily_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    conn.close()
    if daily.empty:
        return None
        
    daily['date'] = pd.to_datetime(daily['date'])
    daily['ema_filter'] = compute_ema(daily['close'], daily_ema_filter)
    daily['macd'], daily['macd_sig'], daily['macd_hist'] = compute_macd(daily['close'], macd_fast, macd_slow, macd_sig)
    daily['rsi'] = compute_rsi(daily['close'], 14)
    daily['atr'] = compute_atr(daily, 14)
    
    daily['month_key'] = daily['date'].dt.to_period('M')
    daily = pd.merge(daily, regime[['month_key', 'month_bull']], on='month_key', how='left')
    daily['month_bull'] = daily['month_bull'].fillna(False)
    
    # --- 3. Simulation ---
    capital = initial_capital
    in_position = False
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
            
            # Exit 1: ATR Trailing Stop Hit
            if today['close'] < trailing_stop:
                exit_triggered = True
                exit_reason = "Trailing Stop"
                
            # Exit 2: MACD Momentum Exhaustion (only if in profit)
            elif today['close'] > entry_price and yesterday['macd_hist'] > 0 and today['macd_hist'] < 0:
                exit_triggered = True
                exit_reason = "MACD Cross Down (Profit)"
                
            # Exit 3: Regime Flip (Nuclear Exit)
            elif not today['month_bull'] and yesterday['month_bull']:
                exit_triggered = True
                exit_reason = "Monthly Regime Flip"
                
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
                
        elif not in_position and i >= 2:
            day_before = daily.iloc[i - 2]
            
            # Regime Filter: Must be in uptrend
            regime_ok = yesterday['month_bull'] and yesterday['close'] > yesterday['ema_filter']
            
            if regime_ok:
                # Entry 1: MACD Hist crosses above 0
                macd_cross_up = (yesterday['macd_hist'] > 0 and day_before['macd_hist'] <= 0)
                
                # Entry 2: RSI hook up from oversold
                rsi_hook = (yesterday['rsi'] > rsi_oversold and day_before['rsi'] <= rsi_oversold)
                
                if macd_cross_up or rsi_hook:
                    entry_price = today['open']
                    entry_date = today['date']
                    shares = int(capital // entry_price)
                    
                    if shares > 0:
                        capital -= shares * entry_price
                        in_position = True
                        highest_close = entry_price
                        atr_val = yesterday['atr'] if not pd.isna(yesterday['atr']) else entry_price * 0.02
                        trailing_stop = entry_price - (atr_val * atr_trail_mult)
    
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
    
    # Benchmark
    first_price = daily.iloc[0]['close']
    last_price = daily.iloc[-1]['close']
    bh_return = (last_price - first_price) / first_price * 100
    bh_shares = int(initial_capital // first_price)
    
    strategy_return = (capital - initial_capital) / initial_capital * 100
    
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['dd'] = (equity_df['peak'] - equity_df['equity']) / equity_df['peak'] * 100
        max_dd = equity_df['dd'].max()
    else:
        max_dd = 0
        
    bh_equity = daily['close'] * bh_shares + (initial_capital - bh_shares * first_price)
    bh_dd = ((bh_equity.cummax() - bh_equity) / bh_equity.cummax() * 100).max()
    
    total_hold = trades_df['hold_days'].sum() if not trades_df.empty else 0
    total_days = (daily['date'].iloc[-1] - daily['date'].iloc[0]).days
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

def optimize_and_blind_test(symbol):
    macds = [(8, 17, 9), (12, 26, 9)] # Fast MACD vs Standard MACD
    rsi_levels = [40, 45]
    atr_trails = [2.0, 3.0, 4.0]
    
    def _best_for_year(target_year):
        best_ret = -999
        best_params = None
        for m, r, a in itertools.product(macds, rsi_levels, atr_trails):
            res = run_hybrid_backtest(symbol, macd_fast=m[0], macd_slow=m[1], macd_sig=m[2], rsi_oversold=r, atr_trail_mult=a)
            if res and not res['trades'].empty:
                t = res['trades'].copy()
                t['exit_date'] = pd.to_datetime(t['exit_date'])
                yr = t[t['exit_date'].dt.year == target_year]
                if len(yr) >= 1:
                    ret = yr['pnl'].sum() / 10000 * 100
                    if ret > best_ret:
                        best_ret = ret
                        best_params = {'m': m, 'r': r, 'a': a}
        return best_params

    def _test_params(params, target_year):
        res = run_hybrid_backtest(symbol, macd_fast=params['m'][0], macd_slow=params['m'][1], macd_sig=params['m'][2],
                                  rsi_oversold=params['r'], atr_trail_mult=params['a'])
        if res and not res['trades'].empty:
            t = res['trades'].copy()
            t['exit_date'] = pd.to_datetime(t['exit_date'])
            yr = t[t['exit_date'].dt.year == target_year]
            if not yr.empty:
                return yr['pnl'].sum() / 10000 * 100
        return 0.0

    # Overall Best
    best_overall = None
    best_excess = -999
    for m, r, a in itertools.product(macds, rsi_levels, atr_trails):
        res = run_hybrid_backtest(symbol, macd_fast=m[0], macd_slow=m[1], macd_sig=m[2], rsi_oversold=r, atr_trail_mult=a)
        if res and res['num_trades'] >= 2:
            if res['excess'] > best_excess:
                best_excess = res['excess']
                best_overall = res
                
    if not best_overall:
        return None
        
    # Blind Tests
    best_24 = _best_for_year(2024)
    a_test = _test_params(best_24, 2025) if best_24 else 0
    
    best_25 = _best_for_year(2025)
    b_test = _test_params(best_25, 2024) if best_25 else 0
    
    if a_test > 0 and b_test > 0:
        verdict = "STRONG"
    elif a_test > 0 or b_test > 0:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"
        
    best_overall['blind_a'] = a_test
    best_overall['blind_b'] = b_test
    best_overall['verdict'] = verdict
    return best_overall

if __name__ == "__main__":
    symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    results = []
    
    for s in symbols:
        print(f"  Optimizing {s}...", flush=True)
        r = optimize_and_blind_test(s)
        if r:
            results.append(r)
            
    print(f"\n{'='*120}")
    print(f"  HYBRID MOMENTUM RESULTS (vs Buy-and-Hold)")
    print(f"{'='*120}")
    print(f"{'Sym':<6} {'MACD':<10} {'RSI':<3} {'ATR':<3} {'Strat':>8} {'B&H':>8} {'Excess':>8} "
          f"{'DD':>6} {'BH DD':>6} {'Trd':>4} {'WR':>5} {'Hold':>5} {'InMkt':>6} "
          f"{'Bld25':>7} {'Bld24':>7} {'Verdict':>8}")
    print("-" * 120)
    for r in results:
        m = r['params']['macd']
        ro = r['params']['rsi_os']
        at = r['params']['atr_trail']
        print(f"{r['symbol']:<6} {m:<10} {ro:<3} {at:<3.1f} "
              f"{r['strategy_return']:>+7.1f}% {r['bh_return']:>+7.1f}% {r['excess']:>+7.1f}% "
              f"{r['max_dd']:>5.1f}% {r['bh_dd']:>5.1f}% "
              f"{r['num_trades']:>4} {r['win_rate']:>4.0f}% {r['avg_hold']:>4.0f}d {r['pct_in_market']:>5.0f}% "
              f"{r['blind_a']:>+6.1f}% {r['blind_b']:>+6.1f}% {r['verdict']:>8}")
    
    beating = [r for r in results if r['excess'] > 0]
    strong = [r for r in results if r['verdict'] == 'STRONG']
    print(f"\nBeating buy-and-hold (Overall): {len(beating)}/{len(results)}")
    print(f"Blind test STRONG pass: {len(strong)}/{len(results)}")
    if beating:
        print(f"Symbols beating B&H: {', '.join(r['symbol'] for r in beating)}")
