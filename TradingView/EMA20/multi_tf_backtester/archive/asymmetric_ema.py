"""
Asymmetric EMA Trend System
============================
Core insight: The problem with EMA20 is symmetric entry/exit = too slow.

Fix: Use DIFFERENT EMAs for entry vs exit.
- FAST EMA for ENTRY (e.g. EMA5): catches the trend start quickly
- SLOW EMA for EXIT (e.g. EMA50): stays in the trend through normal pullbacks

Monthly regime filter stays to avoid counter-trend longs.
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


def compute_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()


def run_asymmetric_backtest(symbol, initial_capital=10000,
                            entry_ema=5, exit_ema=50,
                            min_slope_pct=0.005,
                            exit_days_below=2,
                            lookback_months=3):
    """
    Asymmetric EMA trend-following:
    - ENTER when Monthly bull regime + Daily closes above entry_ema (fast)
    - EXIT when Daily closes below exit_ema for exit_days_below days (slow)
    - Also exit on Monthly regime flip
    """
    conn = get_db_connection()
    
    # Monthly regime
    monthly = pd.read_sql(
        f"SELECT * FROM monthly_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    if monthly.empty:
        conn.close()
        return None
    
    monthly['date'] = pd.to_datetime(monthly['date'])
    monthly['ema_20'] = compute_ema(monthly['close'], 20)
    monthly['ema_slope'] = monthly['ema_20'] - monthly['ema_20'].shift(lookback_months)
    monthly['slope_pct'] = monthly['ema_slope'] / monthly['close']
    monthly['is_bull'] = (monthly['close'] > monthly['ema_20']) & (monthly['slope_pct'] > min_slope_pct)
    
    regime = monthly[['date', 'is_bull']].copy()
    regime['date'] = regime['date'] + pd.DateOffset(months=1)
    regime['month_key'] = regime['date'].dt.to_period('M')
    
    # Daily bars
    daily = pd.read_sql(
        f"SELECT * FROM daily_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    conn.close()
    
    if daily.empty:
        return None
    
    daily['date'] = pd.to_datetime(daily['date'])
    daily['entry_ema'] = compute_ema(daily['close'], entry_ema)
    daily['exit_ema'] = compute_ema(daily['close'], exit_ema)
    daily['atr'] = compute_atr(daily, 14)
    daily['month_key'] = daily['date'].dt.to_period('M')
    
    daily = pd.merge(daily, regime[['month_key', 'is_bull']], on='month_key', how='left')
    daily['is_bull'] = daily['is_bull'].fillna(False)
    
    # Simulation
    capital = initial_capital
    in_position = False
    entry_price = 0.0
    entry_date = None
    shares = 0
    days_below_exit = 0
    
    trades = []
    equity = []
    
    for i in range(1, len(daily)):
        today = daily.iloc[i]
        yesterday = daily.iloc[i - 1]
        
        port_val = capital + (shares * today['close'] if in_position else 0)
        equity.append({'date': today['date'], 'equity': port_val})
        
        if in_position:
            # Count consecutive days below exit EMA
            if today['close'] < today['exit_ema']:
                days_below_exit += 1
            else:
                days_below_exit = 0
            
            exit_triggered = False
            exit_reason = ''
            
            # Exit: N days below slow exit EMA
            if days_below_exit >= exit_days_below:
                exit_triggered = True
                exit_reason = f'{exit_days_below}d below EMA{exit_ema}'
            
            # Exit: Monthly regime flip
            if not today['is_bull'] and yesterday['is_bull']:
                exit_triggered = True
                exit_reason = 'Regime flip'
            
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
                days_below_exit = 0
        
        elif not in_position and i >= 2:
            day_before = daily.iloc[i - 2]
            
            # Entry: yesterday crossed ABOVE fast entry EMA, regime is bull
            cross_up = (yesterday['close'] > yesterday['entry_ema'] and
                       day_before['close'] <= day_before['entry_ema'])
            
            if yesterday['is_bull'] and cross_up:
                entry_price = today['open']
                entry_date = today['date']
                shares = int(capital // entry_price)
                
                if shares > 0:
                    capital -= shares * entry_price
                    in_position = True
                    days_below_exit = 0
    
    # Close open position
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
    
    # Buy-and-hold benchmark
    first_price = daily.iloc[0]['close']
    last_price = daily.iloc[-1]['close']
    bh_return = (last_price - first_price) / first_price * 100
    bh_shares = int(initial_capital // first_price)
    
    strategy_return = (capital - initial_capital) / initial_capital * 100
    
    # Drawdowns
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['dd'] = (equity_df['peak'] - equity_df['equity']) / equity_df['peak'] * 100
        max_dd = equity_df['dd'].max()
    else:
        max_dd = 0
    
    bh_equity = daily['close'] * bh_shares + (initial_capital - bh_shares * first_price)
    bh_dd = ((bh_equity.cummax() - bh_equity) / bh_equity.cummax() * 100).max()
    
    # Time in market
    total_hold = trades_df['hold_days'].sum() if not trades_df.empty else 0
    total_days = (daily['date'].iloc[-1] - daily['date'].iloc[0]).days
    pct_in = total_hold / total_days * 100 if total_days > 0 else 0
    
    return {
        'symbol': symbol,
        'strategy_return': strategy_return,
        'bh_return': bh_return,
        'excess': strategy_return - bh_return,
        'max_dd': max_dd, 'bh_dd': bh_dd,
        'num_trades': len(trades_df),
        'win_rate': len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100 if not trades_df.empty else 0,
        'avg_hold': trades_df['hold_days'].mean() if not trades_df.empty else 0,
        'pct_in_market': pct_in,
        'trades': trades_df,
        'params': {'entry_ema': entry_ema, 'exit_ema': exit_ema,
                   'exit_days': exit_days_below, 'slope': min_slope_pct}
    }


def optimize_and_blind_test(symbol):
    """Sweep asymmetric EMA combos, find best, blind test both directions."""
    
    # Asymmetric EMA grid: fast entry × slow exit
    entry_emas = [3, 5, 8, 10]
    exit_emas = [30, 40, 50]
    exit_days_list = [1, 2, 3]
    slope_list = [0.003, 0.005]
    
    def _best_for_year(target_year):
        best_ret = -999
        best_params = None
        for ee, xe, ed, sl in itertools.product(entry_emas, exit_emas, exit_days_list, slope_list):
            if ee >= xe:
                continue  # Entry must be faster than exit
            r = run_asymmetric_backtest(symbol, entry_ema=ee, exit_ema=xe,
                                        exit_days_below=ed, min_slope_pct=sl)
            if r and not r['trades'].empty:
                t = r['trades'].copy()
                t['exit_date'] = pd.to_datetime(t['exit_date'])
                yr = t[t['exit_date'].dt.year == target_year]
                if len(yr) >= 1:
                    ret = yr['pnl'].sum() / 10000 * 100
                    if ret > best_ret:
                        best_ret = ret
                        best_params = {'entry_ema': ee, 'exit_ema': xe,
                                      'exit_days': ed, 'slope': sl, 'return': ret}
        return best_params
    
    def _test_params(params, target_year):
        r = run_asymmetric_backtest(symbol, entry_ema=params['entry_ema'],
                                    exit_ema=params['exit_ema'],
                                    exit_days_below=params['exit_days'],
                                    min_slope_pct=params['slope'])
        if r and not r['trades'].empty:
            t = r['trades'].copy()
            t['exit_date'] = pd.to_datetime(t['exit_date'])
            yr = t[t['exit_date'].dt.year == target_year]
            if not yr.empty:
                return yr['pnl'].sum() / 10000 * 100
        return 0.0
    
    # Find overall best
    best_overall = None
    best_excess = -999
    for ee, xe, ed, sl in itertools.product(entry_emas, exit_emas, exit_days_list, slope_list):
        if ee >= xe:
            continue
        r = run_asymmetric_backtest(symbol, entry_ema=ee, exit_ema=xe,
                                    exit_days_below=ed, min_slope_pct=sl)
        if r and r['num_trades'] >= 2:
            if r['excess'] > best_excess:
                best_excess = r['excess']
                best_overall = r
    
    if best_overall is None:
        return None
    
    # Blind tests
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
    
    return {
        'symbol': symbol,
        'strat_ret': best_overall['strategy_return'],
        'bh_ret': best_overall['bh_return'],
        'excess': best_overall['excess'],
        'max_dd': best_overall['max_dd'],
        'bh_dd': best_overall['bh_dd'],
        'trades': best_overall['num_trades'],
        'wr': best_overall['win_rate'],
        'avg_hold': best_overall['avg_hold'],
        'in_mkt': best_overall['pct_in_market'],
        'blind_a': a_test, 'blind_b': b_test,
        'verdict': verdict,
        'entry_ema': best_overall['params']['entry_ema'],
        'exit_ema': best_overall['params']['exit_ema'],
        'exit_days': best_overall['params']['exit_days'],
        'slope': best_overall['params']['slope']
    }


if __name__ == "__main__":
    symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    
    results = []
    for s in symbols:
        print(f"  Optimizing {s}...", flush=True)
        r = optimize_and_blind_test(s)
        if r:
            results.append(r)
    
    print(f"\n{'='*120}")
    print(f"  ASYMMETRIC EMA RESULTS (vs Buy-and-Hold)")
    print(f"{'='*120}")
    print(f"{'Sym':<6} {'Entry':>6} {'Exit':>5} {'ED':>3} {'Strat':>8} {'B&H':>8} {'Excess':>8} "
          f"{'DD':>6} {'BH DD':>6} {'Trd':>4} {'WR':>5} {'Hold':>5} {'InMkt':>6} "
          f"{'Bld25':>7} {'Bld24':>7} {'Verdict':>8}")
    print("-" * 120)
    for r in results:
        print(f"{r['symbol']:<6} EMA{r['entry_ema']:<3} EMA{r['exit_ema']:<3} {r['exit_days']:>3} "
              f"{r['strat_ret']:>+7.1f}% {r['bh_ret']:>+7.1f}% {r['excess']:>+7.1f}% "
              f"{r['max_dd']:>5.1f}% {r['bh_dd']:>5.1f}% "
              f"{r['trades']:>4} {r['wr']:>4.0f}% {r['avg_hold']:>4.0f}d {r['in_mkt']:>5.0f}% "
              f"{r['blind_a']:>+6.1f}% {r['blind_b']:>+6.1f}% {r['verdict']:>8}")
    
    beating = [r for r in results if r['excess'] > 0]
    strong = [r for r in results if r['verdict'] == 'STRONG']
    print(f"\nBeating buy-and-hold: {len(beating)}/{len(results)}")
    print(f"Blind test STRONG pass: {len(strong)}/{len(results)}")
    if beating:
        print(f"Symbols beating B&H: {', '.join(r['symbol'] for r in beating)}")
