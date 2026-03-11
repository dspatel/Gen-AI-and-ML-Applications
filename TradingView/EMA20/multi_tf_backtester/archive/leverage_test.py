"""
Leveraged Alpha Engine Test
===========================
Runs the Hybrid Momentum strategy on 2x Leveraged ETFs (SSO, QLD)
and compares it directly to holding the 1x underlying native ETF (SPY, QQQ).
"""
import sqlite3
import pandas as pd
import os
from hybrid_momentum import run_hybrid_backtest

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')

def get_native_bh(symbol, initial_capital=10000):
    conn = sqlite3.connect(DB_PATH)
    daily = pd.read_sql(f"SELECT close FROM daily_bars WHERE symbol='{symbol}' ORDER BY date", conn)
    conn.close()
    
    if daily.empty:
        return 0.0, 0.0
        
    first_price = daily.iloc[0]['close']
    last_price = daily.iloc[-1]['close']
    bh_return = (last_price - first_price) / first_price * 100
    
    bh_shares = int(initial_capital // first_price)
    bh_equity = daily['close'] * bh_shares + (initial_capital - bh_shares * first_price)
    bh_dd = ((bh_equity.cummax() - bh_equity) / bh_equity.cummax() * 100).max()
    
    return bh_return, bh_dd

if __name__ == "__main__":
    pairs = [
        {"lev": "SSO", "native": "SPY", "macd": (12,26,9), "rsi": 40, "atr": 4.0},
        {"lev": "QLD", "native": "QQQ", "macd": (12,26,9), "rsi": 40, "atr": 3.0}
    ]
    
    results = []
    
    for p in pairs:
        print(f"Testing {p['lev']} (Level for {p['native']})...")
        
        # Run standard hybrid momentum on the Leveraged ETF
        res_lev = run_hybrid_backtest(p['lev'], macd_fast=p['macd'][0], macd_slow=p['macd'][1], 
                                      macd_sig=p['macd'][2], rsi_oversold=p['rsi'], atr_trail_mult=p['atr'])
        
        # Get 1x Native Buy & Hold baseline
        nat_ret, nat_dd = get_native_bh(p['native'])
        
        if res_lev:
            results.append({
                'lev_sym': p['lev'],
                'nat_sym': p['native'],
                'strat_ret': res_lev['strategy_return'],
                'strat_dd': res_lev['max_dd'],
                'lev_bh_ret': res_lev['bh_return'],
                'lev_bh_dd': res_lev['bh_dd'],
                'nat_bh_ret': nat_ret,
                'nat_bh_dd': nat_dd,
                'wr': res_lev['win_rate'],
                'trades': res_lev['num_trades']
            })

    print(f"\n{'='*110}")
    print("  LEVERAGED ALPHA ENGINE RESULTS")
    print(f"{'='*110}")
    print(f"{'Strategy':<12} | {'Native Buy&Hold':<20} | {'Leveraged Buy&Hold (Baseline)'}")
    print("-" * 110)
    for r in results:
        print(f"[{r['lev_sym']} System]")
        print(f"  Return: {r['strat_ret']:>+7.1f}%  |  {r['nat_sym']} B&H: {r['nat_bh_ret']:>+7.1f}%  |  {r['lev_sym']} B&H: {r['lev_bh_ret']:>+7.1f}%")
        print(f"  Max DD: {r['strat_dd']:>6.1f}%   |  {r['nat_sym']} DD : {r['nat_bh_dd']:>6.1f}%   |  {r['lev_sym']} DD : {r['lev_bh_dd']:>6.1f}%")
        print(f"  Trades: {r['trades']} (Win Rate: {r['wr']:.0f}%)")
        print(f"  Verdict vs Native: {'✅ BEATS NATIVE' if r['strat_ret'] > r['nat_bh_ret'] else '❌ LOSES TO NATIVE'}")
        print("-" * 50)
