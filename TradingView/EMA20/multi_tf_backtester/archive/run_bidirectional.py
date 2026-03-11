from bidirectional_hybrid import run_bidirectional_backtest
import pandas as pd

def format_res(res):
    if not res: return "No data."
    return f"""
    Overall Strategy Return: {res['strategy_return']:+.1f}%
    Native B&H Return:       {res['bh_return']:+.1f}%
    Strategy Max Drawdown:   {res['max_dd']:.1f}%
    Native Max Drawdown:     {res['bh_dd']:.1f}%
    Total Trades: {res['num_trades']}
       Longs:  {res['num_longs']}
       Shorts: {res['num_shorts']}
    Win Rate: {res['win_rate']:.0f}%
    """

print("==================================================================")
print("  BIDIRECTIONAL HYBRID ENGINE: 2021 - 2026 (FULL CYCLE)")
print("==================================================================")
print("\n[TEST 1] QQQ Native -> QLD Long (2x), SQQQ Short (3x)")
res_qlsq = run_bidirectional_backtest('QQQ', 'QLD', 'SQQQ', macd_fast=12, macd_slow=26, macd_sig=9, rsi_oversold=40, rsi_overbought=60, atr_trail_mult=3.0)
print(format_res(res_qlsq))

print("\n--- Yearly Breakdown for QLD/SQQQ Strategy ---")
if res_qlsq and not res_qlsq['trades'].empty:
    trades = res_qlsq['trades']
    trades['exit_date'] = pd.to_datetime(trades['exit_date'])
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        y_trades = trades[trades['exit_date'].dt.year == y]
        if len(y_trades) > 0:
            pnl_pct_sum = y_trades['pnl_pct'].sum()
            longs = len(y_trades[y_trades['type']=='LONG'])
            shorts = len(y_trades[y_trades['type']=='SHORT'])
            wins = len(y_trades[y_trades['pnl'] > 0])
            wr = wins/len(y_trades)*100
            print(f"  {y}: Total Trade Return = {pnl_pct_sum:+.1f}% | {longs} Longs, {shorts} Shorts | WR: {wr:.0f}%")
        else:
            print(f"  {y}: No closed trades.")

print("\n\n[TEST 2] QQQ Native -> QLD Long (2x), PSQ Short (1x)")
res_qlps = run_bidirectional_backtest('QQQ', 'QLD', 'PSQ', macd_fast=12, macd_slow=26, macd_sig=9, rsi_oversold=40, rsi_overbought=60, atr_trail_mult=3.0)
print(format_res(res_qlps))
