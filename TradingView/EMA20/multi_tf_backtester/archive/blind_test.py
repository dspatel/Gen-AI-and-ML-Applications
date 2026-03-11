"""
Bi-Directional Walk-Forward Blind Test
=======================================
Tests the strategy robustness by training and testing in BOTH directions:
  - Direction A: Train on 2024, blindly test on 2025
  - Direction B: Train on 2025, blindly test on 2024

If the strategy is real (not overfit), it should be profitable in BOTH
directions. If it only works one way, we're fitting to a specific market regime.
"""
import pandas as pd
import itertools
import logging
from backtest_runner import run_backtest

logging.basicConfig(level=logging.WARNING, format='%(message)s')


def _find_best_params(symbol, target_year, timeframe='1h'):
    """Sweep all combos and return the best params based on target_year trades only."""
    atr_multipliers = [1.0, 1.5, 2.0]
    rr_targets = [2.0, 3.0, 4.0]
    pullback_list = [2, 3, 5]
    cooldown_list = [10, 20]
    slope_list = [0.003, 0.005, 0.01]
    
    best_ret = -999
    best_params = None
    best_stats = None
    
    for atr, rr, pb, cd, sl in itertools.product(
        atr_multipliers, rr_targets, pullback_list, cooldown_list, slope_list
    ):
        df = run_backtest(
            symbol, exit_strategy='atr_rr',
            atr_multiplier=atr, rr_target=rr,
            cooldown_bars=cd, min_pullback_bars=pb,
            min_slope_pct=sl, timeframe=timeframe
        )
        
        if df.empty:
            continue
            
        df['exit_time'] = pd.to_datetime(df['exit_time'])
        df_year = df[df['exit_time'].dt.year == target_year]
        
        if len(df_year) < 3:
            continue
        
        ret = df_year['pnl'].sum() / 10000 * 100
        if ret > best_ret:
            best_ret = ret
            best_params = {'atr': atr, 'rr': rr, 'pullback': pb, 'cooldown': cd, 'slope': sl}
            wins = len(df_year[df_year['pnl'] > 0])
            gp = df_year[df_year['pnl'] > 0]['pnl'].sum()
            gl = abs(df_year[df_year['pnl'] <= 0]['pnl'].sum())
            best_stats = {
                'return': ret, 'trades': len(df_year),
                'win_rate': wins / len(df_year) * 100,
                'pf': gp / gl if gl > 0 else float('inf')
            }
    
    return best_params, best_stats


def _test_params(symbol, params, target_year, timeframe='1h'):
    """Run a specific param set and return stats for target_year only."""
    df = run_backtest(
        symbol, exit_strategy='atr_rr',
        atr_multiplier=params['atr'], rr_target=params['rr'],
        cooldown_bars=params['cooldown'], min_pullback_bars=params['pullback'],
        min_slope_pct=params['slope'], timeframe=timeframe
    )
    
    if df.empty:
        return {'return': 0, 'trades': 0, 'win_rate': 0, 'pf': 0}
    
    df['exit_time'] = pd.to_datetime(df['exit_time'])
    df_year = df[df['exit_time'].dt.year == target_year]
    
    if df_year.empty:
        return {'return': 0, 'trades': 0, 'win_rate': 0, 'pf': 0}
    
    ret = df_year['pnl'].sum() / 10000 * 100
    wins = len(df_year[df_year['pnl'] > 0])
    wr = wins / len(df_year) * 100 if len(df_year) > 0 else 0
    gp = df_year[df_year['pnl'] > 0]['pnl'].sum()
    gl = abs(df_year[df_year['pnl'] <= 0]['pnl'].sum())
    pf = gp / gl if gl > 0 else float('inf')
    
    return {'return': ret, 'trades': len(df_year), 'win_rate': wr, 'pf': pf}


def run_bidirectional_blind_test(symbol, timeframe='1h'):
    print(f"\n{'='*70}")
    print(f"  BI-DIRECTIONAL BLIND TEST: {symbol}")
    print(f"{'='*70}")
    
    # Direction A: Train 2024, Test 2025
    print("\n  [A] Train on 2024 → Blind test on 2025")
    params_a, train_a = _find_best_params(symbol, 2024, timeframe)
    if params_a:
        test_a = _test_params(symbol, params_a, 2025, timeframe)
        print(f"      Train 2024: Ret {train_a['return']:+.2f}% | WR {train_a['win_rate']:.0f}% | PF {train_a['pf']:.2f} | Trades {train_a['trades']}")
        print(f"      Test  2025: Ret {test_a['return']:+.2f}% | WR {test_a['win_rate']:.0f}% | PF {test_a['pf']:.2f} | Trades {test_a['trades']}")
        print(f"      Config: ATR {params_a['atr']}x RR {params_a['rr']}R PB {params_a['pullback']} CD {params_a['cooldown']} SL {params_a['slope']}")
    else:
        train_a = {'return': 0, 'win_rate': 0, 'pf': 0, 'trades': 0}
        test_a = {'return': 0, 'win_rate': 0, 'pf': 0, 'trades': 0}
        print("      No valid config found for 2024.")
        
    # Direction B: Train 2025, Test 2024
    print("\n  [B] Train on 2025 → Blind test on 2024")
    params_b, train_b = _find_best_params(symbol, 2025, timeframe)
    if params_b:
        test_b = _test_params(symbol, params_b, 2024, timeframe)
        print(f"      Train 2025: Ret {train_b['return']:+.2f}% | WR {train_b['win_rate']:.0f}% | PF {train_b['pf']:.2f} | Trades {train_b['trades']}")
        print(f"      Test  2024: Ret {test_b['return']:+.2f}% | WR {test_b['win_rate']:.0f}% | PF {test_b['pf']:.2f} | Trades {test_b['trades']}")
        print(f"      Config: ATR {params_b['atr']}x RR {params_b['rr']}R PB {params_b['pullback']} CD {params_b['cooldown']} SL {params_b['slope']}")
    else:
        train_b = {'return': 0, 'win_rate': 0, 'pf': 0, 'trades': 0}
        test_b = {'return': 0, 'win_rate': 0, 'pf': 0, 'trades': 0}
        print("      No valid config found for 2025.")
    
    # Verdict
    a_pass = test_a['return'] > 0
    b_pass = test_b['return'] > 0
    
    if a_pass and b_pass:
        verdict = "STRONG PASS"
    elif a_pass or b_pass:
        verdict = "PARTIAL PASS"
    else:
        verdict = "FAIL"
    
    print(f"\n  Verdict: {verdict}")
    
    return {
        'symbol': symbol,
        'a_train': train_a['return'], 'a_test': test_a['return'],
        'b_train': train_b['return'], 'b_test': test_b['return'],
        'a_test_wr': test_a['win_rate'], 'b_test_wr': test_b['win_rate'],
        'a_test_pf': test_a['pf'], 'b_test_pf': test_b['pf'],
        'a_test_trades': test_a['trades'], 'b_test_trades': test_b['trades'],
        'verdict': verdict
    }


if __name__ == "__main__":
    symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    
    all_results = []
    for s in symbols:
        result = run_bidirectional_blind_test(s, timeframe='1h')
        all_results.append(result)
    
    # Final summary table
    print(f"\n\n{'='*90}")
    print("  FINAL BLIND TEST SUMMARY")
    print(f"{'='*90}")
    print(f"{'Symbol':<7} {'Train24→Test25':>15} {'Train25→Test24':>15} {'Test25 WR':>10} {'Test24 WR':>10} {'Verdict':>14}")
    print("-" * 75)
    for r in all_results:
        print(f"{r['symbol']:<7} {r['a_test']:>+14.2f}% {r['b_test']:>+14.2f}% "
              f"{r['a_test_wr']:>9.0f}% {r['b_test_wr']:>9.0f}% {r['verdict']:>14}")
    
    strong = [r for r in all_results if r['verdict'] == 'STRONG PASS']
    partial = [r for r in all_results if r['verdict'] == 'PARTIAL PASS']
    fail = [r for r in all_results if r['verdict'] == 'FAIL']
    
    print(f"\nStrong Pass: {len(strong)}/9 | Partial: {len(partial)}/9 | Fail: {len(fail)}/9")
    if strong:
        print(f"Symbols passing BOTH directions: {', '.join(r['symbol'] for r in strong)}")
