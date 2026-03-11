"""
Trend-Following Regime Backtester
=================================
Entry: Monthly bullish + Daily crosses above Daily EMA20 → go ALL-IN at next day's open.
Hold:  Ride the trend.
Exit:  Daily closes below EMA20 for N consecutive days, OR Monthly regime flips,
       OR ATR trailing stop is hit.
"""
import sqlite3
import pandas as pd
import numpy as np
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')


def get_db_connection():
    return sqlite3.connect(DB_PATH)


def compute_ema(series, period=20):
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(period).mean()


def run_trend_backtest(symbol, initial_capital=10000,
                       exit_days_below_ema=2,
                       atr_trail_multiplier=3.0,
                       min_slope_pct=0.005,
                       lookback_months_for_slope=3):
    """
    Trend-following backtest using Daily bars with Monthly regime filter.
    
    Returns a dict with trade log, equity curve, and benchmark comparison.
    """
    conn = get_db_connection()
    
    # === Load Monthly data ===
    monthly_df = pd.read_sql(
        f"SELECT * FROM monthly_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    if monthly_df.empty:
        conn.close()
        return None
    
    monthly_df['date'] = pd.to_datetime(monthly_df['date'])
    monthly_df['ema_20'] = compute_ema(monthly_df['close'], 20)
    monthly_df['ema_slope'] = monthly_df['ema_20'] - monthly_df['ema_20'].shift(lookback_months_for_slope)
    monthly_df['slope_pct'] = monthly_df['ema_slope'] / monthly_df['close']
    monthly_df['is_bull_regime'] = (
        (monthly_df['close'] > monthly_df['ema_20']) &
        (monthly_df['slope_pct'] > min_slope_pct)
    )
    
    # Shift monthly by 1 month (no look-ahead)
    monthly_regime = monthly_df[['date', 'is_bull_regime']].copy()
    monthly_regime['date'] = monthly_regime['date'] + pd.DateOffset(months=1)
    monthly_regime['month_key'] = monthly_regime['date'].dt.to_period('M')
    
    # === Load Daily data ===
    daily_df = pd.read_sql(
        f"SELECT * FROM daily_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    conn.close()
    
    if daily_df.empty:
        return None
    
    daily_df['date'] = pd.to_datetime(daily_df['date'])
    daily_df['ema_20'] = compute_ema(daily_df['close'], 20)
    daily_df['atr'] = compute_atr(daily_df, 14)
    daily_df['month_key'] = daily_df['date'].dt.to_period('M')
    
    # Merge monthly regime
    daily_df = pd.merge(daily_df, monthly_regime[['month_key', 'is_bull_regime']],
                        on='month_key', how='left')
    daily_df['is_bull_regime'] = daily_df['is_bull_regime'].fillna(False)
    
    # === Simulation ===
    capital = initial_capital
    in_position = False
    entry_price = 0.0
    entry_date = None
    shares = 0
    trailing_stop = 0.0
    highest_close = 0.0
    days_below_ema = 0
    
    trades = []
    equity_curve = []
    
    for i in range(1, len(daily_df)):
        today = daily_df.iloc[i]
        yesterday = daily_df.iloc[i - 1]
        
        # Track equity
        if in_position:
            portfolio_value = capital + shares * today['close']
        else:
            portfolio_value = capital
        equity_curve.append({'date': today['date'], 'equity': portfolio_value})
        
        # === CHECK EXIT (if in position) ===
        if in_position:
            # Update trailing stop
            if today['close'] > highest_close:
                highest_close = today['close']
                atr_val = today['atr'] if not pd.isna(today['atr']) else highest_close * 0.02
                trailing_stop = highest_close - (atr_val * atr_trail_multiplier)
            
            # Count consecutive days below EMA
            if today['close'] < today['ema_20']:
                days_below_ema += 1
            else:
                days_below_ema = 0
            
            exit_triggered = False
            exit_reason = ''
            
            # Exit 1: N consecutive closes below daily EMA20
            if days_below_ema >= exit_days_below_ema:
                exit_triggered = True
                exit_reason = f'{exit_days_below_ema}d below EMA'
            
            # Exit 2: Monthly regime flipped bearish
            if not today['is_bull_regime'] and yesterday['is_bull_regime']:
                exit_triggered = True
                exit_reason = 'Monthly regime flip'
            
            # Exit 3: ATR trailing stop hit
            if today['close'] < trailing_stop:
                exit_triggered = True
                exit_reason = 'ATR trailing stop'
            
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
                    'entry_date': entry_date,
                    'entry_price': entry_price,
                    'exit_date': exit_date,
                    'exit_price': exit_price,
                    'shares': shares,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'hold_days': hold_days,
                    'exit_reason': exit_reason,
                    'capital_after': capital
                })
                
                in_position = False
                shares = 0
                days_below_ema = 0
        
        # === CHECK ENTRY (if not in position) ===
        elif not in_position:
            # Entry: Yesterday's regime was bullish AND yesterday's close crossed above EMA
            # (yesterday was above EMA but day-before-yesterday was below)
            if i >= 2:
                day_before = daily_df.iloc[i - 2]
                
                cross_up = (yesterday['close'] > yesterday['ema_20'] and 
                           day_before['close'] <= day_before['ema_20'])
                
                if yesterday['is_bull_regime'] and cross_up:
                    entry_price = today['open']
                    entry_date = today['date']
                    shares = int(capital // entry_price)
                    
                    if shares > 0:
                        cost = shares * entry_price
                        capital -= cost
                        in_position = True
                        highest_close = entry_price
                        atr_val = yesterday['atr'] if not pd.isna(yesterday['atr']) else entry_price * 0.02
                        trailing_stop = entry_price - (atr_val * atr_trail_multiplier)
                        days_below_ema = 0
    
    # Close open position at end of data
    if in_position:
        last = daily_df.iloc[-1]
        exit_price = last['close']
        exit_date = last['date']
        revenue = shares * exit_price
        capital += revenue
        pnl = revenue - (shares * entry_price)
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        hold_days = (exit_date - entry_date).days
        
        trades.append({
            'symbol': symbol,
            'entry_date': entry_date,
            'entry_price': entry_price,
            'exit_date': exit_date,
            'exit_price': exit_price,
            'shares': shares,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'hold_days': hold_days,
            'exit_reason': 'End of data',
            'capital_after': capital
        })
    
    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_curve)
    
    # === Buy-and-hold benchmark ===
    first_price = daily_df.iloc[0]['close']
    last_price = daily_df.iloc[-1]['close']
    bh_return = (last_price - first_price) / first_price * 100
    bh_shares = int(initial_capital // first_price)
    bh_final = capital if trades_df.empty else trades_df['capital_after'].iloc[-1]
    
    strategy_return = ((bh_final if not trades_df.empty else initial_capital) - initial_capital) / initial_capital * 100
    
    # Max drawdown
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown'] = (equity_df['peak'] - equity_df['equity']) / equity_df['peak'] * 100
        max_dd = equity_df['drawdown'].max()
    else:
        max_dd = 0
    
    # Buy-and-hold drawdown
    bh_equity = daily_df['close'] * bh_shares + (initial_capital - bh_shares * first_price)
    bh_peak = bh_equity.cummax()
    bh_dd = ((bh_peak - bh_equity) / bh_peak * 100).max()
    
    # Time in market
    if not trades_df.empty:
        total_days_in = trades_df['hold_days'].sum()
        total_days = (daily_df['date'].iloc[-1] - daily_df['date'].iloc[0]).days
        pct_in_market = total_days_in / total_days * 100 if total_days > 0 else 0
    else:
        pct_in_market = 0
    
    result = {
        'symbol': symbol,
        'trades': trades_df,
        'equity': equity_df,
        'strategy_return': strategy_return,
        'bh_return': bh_return,
        'excess_return': strategy_return - bh_return,
        'max_drawdown': max_dd,
        'bh_max_drawdown': bh_dd,
        'num_trades': len(trades_df),
        'pct_in_market': pct_in_market,
        'params': {
            'exit_days': exit_days_below_ema,
            'atr_trail': atr_trail_multiplier,
            'slope': min_slope_pct
        }
    }
    
    if not trades_df.empty:
        result['win_rate'] = len(trades_df[trades_df['pnl'] > 0]) / len(trades_df) * 100
        result['avg_hold_days'] = trades_df['hold_days'].mean()
    else:
        result['win_rate'] = 0
        result['avg_hold_days'] = 0
    
    return result


if __name__ == "__main__":
    symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    
    print(f"\n{'='*90}")
    print(f"  TREND-FOLLOWING REGIME BACKTEST (Daily Bars)")
    print(f"{'='*90}")
    print(f"{'Symbol':<7} {'Strategy':>10} {'Buy&Hold':>10} {'Excess':>8} {'MaxDD':>7} {'BH DD':>7} {'Trades':>7} {'WinRate':>8} {'AvgHold':>8} {'InMkt':>7}")
    print("-" * 85)
    
    for sym in symbols:
        r = run_trend_backtest(sym)
        if r:
            print(f"{r['symbol']:<7} {r['strategy_return']:>+9.2f}% {r['bh_return']:>+9.2f}% "
                  f"{r['excess_return']:>+7.2f}% {r['max_drawdown']:>6.1f}% {r['bh_max_drawdown']:>6.1f}% "
                  f"{r['num_trades']:>7} {r['win_rate']:>7.0f}% {r['avg_hold_days']:>7.0f}d "
                  f"{r['pct_in_market']:>6.0f}%")
