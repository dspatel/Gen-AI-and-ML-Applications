import itertools
import pandas as pd
from bidirectional_hybrid import run_bidirectional_backtest

macds = [(8,17,9), (12,26,9)]
rsis = [40, 50, 60]
atrs = [1.5, 2.0, 3.0, 4.0]

print("Sweeping Bidirectional Strategy for 2022 Bear Market (QQQ / QLD / SQQQ)...")
best_22_ret = -999
best_overall_ret = -999
best_22_res = None
best_overall_res = None

for m, r, a in itertools.product(macds, rsis, atrs):
    res = run_bidirectional_backtest('QQQ', 'QLD', 'SQQQ', macd_fast=m[0], macd_slow=m[1], macd_sig=m[2],
                                     rsi_oversold=40, rsi_overbought=r, atr_trail_mult=a)
    if res and res['num_trades'] > 5:
        if res['strategy_return'] > best_overall_ret:
            best_overall_ret = res['strategy_return']
            best_overall_res = res
            
        trades = res['trades']
        trades['exit_date'] = pd.to_datetime(trades['exit_date'])
        y_trades = trades[trades['exit_date'].dt.year == 2022]
        if len(y_trades) > 0:
            ret_22 = y_trades['pnl_pct'].sum()
            if ret_22 > best_22_ret:
                best_22_ret = ret_22
                best_22_res = res

with open('sweep_results_2022.txt', 'w', encoding='utf-8') as f:
    f.write(f"--- BEST config for OVERALL RETURN ---\n")
    if best_overall_res:
        res = best_overall_res
        f.write(f"  MACD: {res['num_trades']} trades | WR: {res['win_rate']:.0f}%\n")
        f.write(f"  Overall Ret: {res['strategy_return']:+.1f}% | B&H: {res['bh_return']:+.1f}%\n")

    f.write(f"\n--- BEST config for 2022 BEAR MARKET ---\n")
    if best_22_res:
        res = best_22_res
        trades = res['trades']
        trades['exit_date'] = pd.to_datetime(trades['exit_date'])
        y_trades = trades[trades['exit_date'].dt.year == 2022]
        ret_22 = y_trades['pnl_pct'].sum()
        f.write(f"  2022 Return: {ret_22:+.1f}%\n")
        for idx, row in y_trades.iterrows():
            f.write(f"    {row['type']} {row['symbol']} Entry: {row['entry_date'].date()} | Exit: {row['exit_date'].date()} | PnL: {row['pnl_pct']:+.1f}% | Reason: {row['reason']}\n")
    else:
        f.write("  No config made any trades in 2022.\n")
