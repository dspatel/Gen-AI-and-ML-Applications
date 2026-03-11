import pandas as pd
from signal_engines import PortfolioDataEngine, MacroEngine, RulesEngine
from decision_engine import DecisionEngine

def run():
    db = PortfolioDataEngine()
    db.load_all_data()

    # Calculate Equal Weight Benchmark
    print('Calculating 16-Symbol Equal Weight Benchmark (2021 - 2026)...')
    symbols = [s for s in db.daily.keys() if s != 'SPY']
    start_date = '2021-01-01'
    starting_cap = 100000 / len(symbols)

    bh_equity = pd.Series(0.0, index=db.daily['QQQ'][db.daily['QQQ'].index >= start_date].index)
    for sym in symbols:
        df = db.daily[sym]
        df = df[df.index >= start_date]
        if df.empty: continue
        shares = starting_cap / df.iloc[0]['close']
        sym_eq = df['close'] * shares
        bh_equity = bh_equity.add(sym_eq, fill_value=0)

    bh_ret = (bh_equity.iloc[-1] - 100000) / 100000 * 100
    bh_peak = bh_equity.cummax()
    bh_dd = ((bh_peak - bh_equity) / bh_peak * 100).max()

    print(f'Benchmark Return: +{bh_ret:.1f}% | Max DD: {bh_dd:.1f}%')

    # Run Decision Engine
    macro = MacroEngine(db)
    rules = RulesEngine(db)
    brain = DecisionEngine(initial_capital=100000, max_positions=5, rsi_entry=40, atr_stop=3.0, risk_pct=0.15) # Increased risk usage

    print('Running Portfolio Decision Engine...')
    trades, equity = brain.run_simulation(db, macro, rules, start_date='2021-01-01')

    ret = (equity.iloc[-1]['equity'] - 100000) / 100000 * 100
    wins = trades[trades['pnl_pct'] > 0]
    wr = len(wins) / len(trades) * 100 if len(trades) > 0 else 0
    equity['peak'] = equity['equity'].cummax()
    dd = (equity['peak'] - equity['equity']) / equity['peak'] * 100

    print(f'\\n--- FINAL RESULTS ---')
    print(f'Engine Return: {ret:+.1f}% (vs B&H {bh_ret:+.1f}%)')
    print(f'Engine Max DD: {dd.max():.1f}% (vs B&H {bh_dd:.1f}%)')
    print(f'Total Trades: {len(trades)} | Win Rate: {wr:.1f}%')
    
if __name__ == '__main__':
    run()
