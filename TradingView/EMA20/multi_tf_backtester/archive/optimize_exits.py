import pandas as pd
import logging
import itertools
from backtest_runner import run_backtest

logging.basicConfig(level=logging.WARNING, format='%(message)s')

def optimize_parameters(symbol, timeframe='1h'):
    print(f"\n{'='*60}")
    print(f"  Optimization Sweep: {symbol} ({timeframe})")
    print(f"{'='*60}")
    
    # Parameter grid
    atr_multipliers = [1.0, 1.5, 2.0]
    rr_targets = [2.0, 3.0, 4.0]
    min_pullback_bars_list = [2, 3, 5]
    cooldown_bars_list = [10, 20]
    min_slope_pcts = [0.003, 0.005, 0.01]
    
    results = []
    
    for atr, rr, pullback, cooldown, slope in itertools.product(
        atr_multipliers, rr_targets, min_pullback_bars_list, cooldown_bars_list, min_slope_pcts
    ):
        df_opt = run_backtest(
            symbol, 
            exit_strategy='atr_rr', 
            atr_multiplier=atr, 
            rr_target=rr,
            cooldown_bars=cooldown,
            min_pullback_bars=pullback,
            min_slope_pct=slope,
            timeframe=timeframe
        )
        
        if not df_opt.empty and len(df_opt) >= 3:  # Need at least 3 trades
            df_opt['exit_time'] = pd.to_datetime(df_opt['exit_time'])
            df_opt['year'] = df_opt['exit_time'].dt.year
            
            df_24 = df_opt[df_opt['year'] == 2024]
            df_25 = df_opt[df_opt['year'] == 2025]
            
            total_return = ((df_opt['capital_after'].iloc[-1] - 10000) / 10000) * 100
            ret_24 = (df_24['pnl'].sum() / 10000) * 100 if not df_24.empty else 0
            ret_25 = (df_25['pnl'].sum() / max(1, 10000 + df_24['pnl'].sum())) * 100 if not df_25.empty else 0
            
            wins = len(df_opt[df_opt['pnl'] > 0])
            losses = len(df_opt[df_opt['pnl'] <= 0])
            win_rate = wins / len(df_opt) * 100
            
            gross_profit = df_opt[df_opt['pnl'] > 0]['pnl'].sum()
            gross_loss = abs(df_opt[df_opt['pnl'] <= 0]['pnl'].sum())
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
            
            results.append({
                'symbol': symbol,
                'atr': atr,
                'rr': rr,
                'pullback': pullback,
                'cooldown': cooldown,
                'slope': slope,
                'trades': len(df_opt),
                'win_rate': win_rate,
                'pf': profit_factor,
                'total_ret': total_return,
                'ret_24': ret_24,
                'ret_25': ret_25
            })
            
    res_df = pd.DataFrame(results)
    if not res_df.empty:
        res_df = res_df.sort_values('total_ret', ascending=False)
        print(f"\nTop 5 configurations:")
        print(res_df.head(5).to_string(index=False, float_format='%.2f'))
        print(f"\nTotal parameter combos tested: {len(res_df)}")
        print(f"Profitable combos: {len(res_df[res_df['total_ret'] > 0])}/{len(res_df)}")
        return res_df.iloc[0]
    else:
        print(f"No valid results for {symbol}")
    return None

if __name__ == "__main__":
    symbols_to_test = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "VOO", "SCHG", "AMZN"]
    
    best_overall = {}
    for s in symbols_to_test:
        best = optimize_parameters(s, timeframe='1h')
        if best is not None:
            best_overall[s] = best
             
    print(f"\n\n{'='*70}")
    print("  OVERALL BEST CONFIGURATIONS (Redesigned Strategy)")
    print(f"{'='*70}")
    for s, best in best_overall.items():
        print(f"{s}: ATR {best['atr']}x | Target {best['rr']}R | Pullback {best['pullback']}bars | "
              f"Cooldown {best['cooldown']} | Slope {best['slope']} --> "
              f"Total: {best['total_ret']:.2f}% (2024: {best['ret_24']:.2f}%, 2025: {best['ret_25']:.2f}%) | "
              f"Trades: {best['trades']} | WR: {best['win_rate']:.0f}% | PF: {best['pf']:.2f}")
