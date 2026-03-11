"""
Trend-Following Optimizer + Bi-Directional Blind Test
=====================================================
Sweeps parameters, finds best config, then validates with blind testing.
Compares all results against buy-and-hold.
"""
import pandas as pd
import itertools
import logging
from trend_backtest import run_trend_backtest

logging.basicConfig(level=logging.WARNING, format='%(message)s')


def optimize_and_blind_test(symbol):
    """
    1. Sweep params across FULL data → find overall best
    2. Train on 2024 → blind test 2025
    3. Train on 2025 → blind test 2024
    """
    print(f"\n{'='*75}")
    print(f"  {symbol}")
    print(f"{'='*75}")
    
    # Parameter grid
    exit_days_list = [1, 2, 3]
    atr_trail_list = [2.0, 3.0, 4.0]
    slope_list = [0.003, 0.005, 0.01]
    
    # === PHASE 1: Full sweep to find best overall ===
    all_results = []
    for ed, at, sl in itertools.product(exit_days_list, atr_trail_list, slope_list):
        r = run_trend_backtest(symbol, exit_days_below_ema=ed,
                               atr_trail_multiplier=at, min_slope_pct=sl)
        if r and r['num_trades'] >= 2:
            all_results.append({
                'exit_days': ed, 'atr_trail': at, 'slope': sl,
                'return': r['strategy_return'], 'bh_return': r['bh_return'],
                'excess': r['excess_return'], 'max_dd': r['max_drawdown'],
                'bh_dd': r['bh_max_drawdown'], 'trades': r['num_trades'],
                'wr': r['win_rate'], 'avg_hold': r['avg_hold_days'],
                'in_mkt': r['pct_in_market']
            })
    
    if not all_results:
        print(f"  No valid configs for {symbol}.")
        return None
    
    res_df = pd.DataFrame(all_results).sort_values('excess', ascending=False)
    best = res_df.iloc[0]
    
    print(f"\n  BEST OVERALL: ExitDays={best['exit_days']:.0f} ATR={best['atr_trail']} Slope={best['slope']}")
    print(f"    Strategy: {best['return']:+.2f}% | Buy&Hold: {best['bh_return']:+.2f}% | "
          f"Excess: {best['excess']:+.2f}% | MaxDD: {best['max_dd']:.1f}% vs BH DD: {best['bh_dd']:.1f}%")
    
    # === PHASE 2: Blind test Direction A (Train 2024 → Test 2025) ===
    def _best_for_year(target_year):
        year_results = []
        for ed, at, sl in itertools.product(exit_days_list, atr_trail_list, slope_list):
            r = run_trend_backtest(symbol, exit_days_below_ema=ed,
                                   atr_trail_multiplier=at, min_slope_pct=sl)
            if r and not r['trades'].empty:
                trades = r['trades'].copy()
                trades['exit_date'] = pd.to_datetime(trades['exit_date'])
                yr_trades = trades[trades['exit_date'].dt.year == target_year]
                if len(yr_trades) >= 1:
                    yr_ret = yr_trades['pnl'].sum() / 10000 * 100
                    year_results.append({
                        'exit_days': ed, 'atr_trail': at, 'slope': sl,
                        'return': yr_ret, 'trades': len(yr_trades)
                    })
        if year_results:
            yr_df = pd.DataFrame(year_results).sort_values('return', ascending=False)
            return yr_df.iloc[0]
        return None
    
    def _test_year(params, target_year):
        r = run_trend_backtest(symbol, exit_days_below_ema=int(params['exit_days']),
                               atr_trail_multiplier=params['atr_trail'],
                               min_slope_pct=params['slope'])
        if r and not r['trades'].empty:
            trades = r['trades'].copy()
            trades['exit_date'] = pd.to_datetime(trades['exit_date'])
            yr_trades = trades[trades['exit_date'].dt.year == target_year]
            if not yr_trades.empty:
                return yr_trades['pnl'].sum() / 10000 * 100
        return 0.0
    
    # Direction A
    best_24 = _best_for_year(2024)
    a_test = 0.0
    if best_24 is not None:
        a_test = _test_year(best_24, 2025)
        print(f"\n  BLIND A: Train 2024 ({best_24['return']:+.2f}%) → Test 2025: {a_test:+.2f}%")
    
    # Direction B
    best_25 = _best_for_year(2025)
    b_test = 0.0
    if best_25 is not None:
        b_test = _test_year(best_25, 2024)
        print(f"  BLIND B: Train 2025 ({best_25['return']:+.2f}%) → Test 2024: {b_test:+.2f}%")
    
    # Verdict
    if a_test > 0 and b_test > 0:
        verdict = "STRONG PASS"
    elif a_test > 0 or b_test > 0:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"
    
    print(f"  Verdict: {verdict}")
    
    return {
        'symbol': symbol,
        'best_return': best['return'],
        'bh_return': best['bh_return'],
        'excess': best['excess'],
        'max_dd': best['max_dd'],
        'bh_dd': best['bh_dd'],
        'trades': best['trades'],
        'wr': best['wr'],
        'avg_hold': best['avg_hold'],
        'in_mkt': best['in_mkt'],
        'blind_a': a_test,
        'blind_b': b_test,
        'verdict': verdict,
        'config': f"ED={best['exit_days']:.0f} ATR={best['atr_trail']} SL={best['slope']}"
    }


if __name__ == "__main__":
    symbols = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    
    results = []
    for s in symbols:
        r = optimize_and_blind_test(s)
        if r:
            results.append(r)
    
    print(f"\n\n{'='*110}")
    print("  TREND-FOLLOWING FINAL SUMMARY (vs Buy-and-Hold)")
    print(f"{'='*110}")
    print(f"{'Sym':<6} {'Config':<18} {'Strat':>8} {'B&H':>8} {'Excess':>8} {'DD':>6} {'BH DD':>6} "
          f"{'Trd':>4} {'WR':>5} {'Hold':>5} {'InMkt':>6} {'Blind25':>8} {'Blind24':>8} {'Verdict':>10}")
    print("-" * 110)
    for r in results:
        print(f"{r['symbol']:<6} {r['config']:<18} {r['best_return']:>+7.1f}% {r['bh_return']:>+7.1f}% "
              f"{r['excess']:>+7.1f}% {r['max_dd']:>5.1f}% {r['bh_dd']:>5.1f}% "
              f"{r['trades']:>4.0f} {r['wr']:>4.0f}% {r['avg_hold']:>4.0f}d {r['in_mkt']:>5.0f}% "
              f"{r['blind_a']:>+7.1f}% {r['blind_b']:>+7.1f}% {r['verdict']:>10}")
    
    # Summary
    beating_bh = [r for r in results if r['excess'] > 0]
    strong = [r for r in results if r['verdict'] == 'STRONG PASS']
    print(f"\nBeating buy-and-hold (overall): {len(beating_bh)}/{len(results)}")
    print(f"Passing blind test (both dirs): {len(strong)}/{len(results)}")
