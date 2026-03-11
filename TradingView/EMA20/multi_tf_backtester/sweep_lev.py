import itertools
from hybrid_momentum import run_hybrid_backtest

pairs = [
    {"lev": "SSO", "native": "SPY"},
    {"lev": "QLD", "native": "QQQ"}
]

macds = [(8,17,9), (12,26,9)]
rsis = [40, 45]
atrs = [3.0, 4.0, 5.0]

for p in pairs:
    print(f"\nSweeping {p['lev']}...")
    best_ret = -999
    best_res = None
    
    for m, r, a in itertools.product(macds, rsis, atrs):
        res = run_hybrid_backtest(p['lev'], macd_fast=m[0], macd_slow=m[1], macd_sig=m[2],
                                  rsi_oversold=r, atr_trail_mult=a)
        if res and res['num_trades'] > 5:
            if res['strategy_return'] > best_ret:
                best_ret = res['strategy_return']
                best_res = res
                
    if best_res:
        print(f"BEST {p['lev']}: MACD {best_res['params']['macd']}, RSI {best_res['params']['rsi_os']}, ATR {best_res['params']['atr_trail']}")
        print(f"  Return: {best_res['strategy_return']:+.1f}% | DD: {best_res['max_dd']:.1f}%")
        print(f"  Trades: {best_res['num_trades']} | WR: {best_res['win_rate']:.0f}%")
