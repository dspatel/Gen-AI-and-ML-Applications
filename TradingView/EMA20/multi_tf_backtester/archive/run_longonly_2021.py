from hybrid_momentum import run_hybrid_backtest
from leverage_test import get_native_bh

print("==================================================================")
print("  LONG-ONLY HYBRID ENGINE: 2021 - 2026 (FULL CYCLE)")
print("==================================================================")

nat_ret, nat_dd = get_native_bh('QQQ')

# Run Long-Only on QLD (2x QQQ)
res = run_hybrid_backtest('QLD', macd_fast=12, macd_slow=26, macd_sig=9, rsi_oversold=45, atr_trail_mult=3.0)

if res:
    print(f"[{res['symbol']} System vs Native QQQ]")
    print(f"  Return: {res['strategy_return']:>+7.1f}%  |  QQQ B&H: {nat_ret:>+7.1f}%")
    print(f"  Max DD: {res['max_dd']:>6.1f}%   |  QQQ DD : {nat_dd:>6.1f}%")
    print(f"  Trades: {res['num_trades']} (Win Rate: {res['win_rate']:.0f}%)")
    print(f"  Verdict vs Native: {'✅ BEATS NATIVE' if res['strategy_return'] > nat_ret else '❌ LOSES TO NATIVE'}")
    print("-" * 50)
    
    import pandas as pd
    trades = res['trades']
    trades['exit_date'] = pd.to_datetime(trades['exit_date'])
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        y_trades = trades[trades['exit_date'].dt.year == y]
        if len(y_trades) > 0:
            pnl_pct_sum = y_trades['pnl_pct'].sum()
            wins = len(y_trades[y_trades['pnl'] > 0])
            wr = wins/len(y_trades)*100
            print(f"  {y}: Total Trade Return = {pnl_pct_sum:+.1f}% | {len(y_trades)} Trades | WR: {wr:.0f}%")
        else:
            print(f"  {y}: ZERO TRADES (Safely in Cash)")
else:
    print("No data.")
