import itertools
from hybrid_momentum import run_hybrid_backtest
from leverage_test import get_native_bh

pairs = [
    {"lev": "TQQQ", "native": "QQQ"},
    {"lev": "SOXL", "native": "SOXX"}
]

macds = [(8,17,9), (12,26,9)]
rsis = [40, 45, 50]
atrs = [3.0, 4.0, 5.0, 6.0]

for p in pairs:
    print(f"\nSweeping {p['lev']} (3x Leveraged {p['native']})...")
    best_ret = -999
    best_res = None
    
    # Get native buy & hold to beat
    nat_bh_ret, nat_bh_dd = get_native_bh(p['native'])
    
    for m, r, a in itertools.product(macds, rsis, atrs):
        res = run_hybrid_backtest(p['lev'], macd_fast=m[0], macd_slow=m[1], macd_sig=m[2],
                                  rsi_oversold=r, atr_trail_mult=a)
        if res and res['num_trades'] > 5:
            if res['strategy_return'] > best_ret:
                best_ret = res['strategy_return']
                best_res = res
                
    if best_res:
        lev_bh_ret = best_res['bh_return']
        lev_bh_dd = best_res['bh_dd']
        strat_ret = best_res['strategy_return']
        strat_dd = best_res['max_dd']
        
        print(f"BEST {p['lev']}: MACD {best_res['params']['macd']}, RSI {best_res['params']['rsi_os']}, ATR {best_res['params']['atr_trail']}")
        print(f"[{p['lev']} System]")
        print(f"  Return: {strat_ret:>+7.1f}%  |  {p['native']} B&H: {nat_bh_ret:>+7.1f}%  |  {p['lev']} B&H: {lev_bh_ret:>+7.1f}%")
        print(f"  Max DD: {strat_dd:>6.1f}%   |  {p['native']} DD : {nat_bh_dd:>6.1f}%   |  {p['lev']} DD : {lev_bh_dd:>6.1f}%")
        print(f"  Trades: {best_res['num_trades']} (Win Rate: {best_res['win_rate']:.0f}%)")
        print(f"  Verdict vs Native: {'✅ BEATS NATIVE' if strat_ret > nat_bh_ret else '❌ LOSES TO NATIVE'}")
        print("-" * 50)
