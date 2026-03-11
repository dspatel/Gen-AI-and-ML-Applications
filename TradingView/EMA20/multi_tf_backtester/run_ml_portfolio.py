import pandas as pd
from signal_engines import PortfolioDataEngine, MacroEngine
from ml_engine import MLEngine
from decision_engine import DecisionEngine

def run_ml_backtest():
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

    print(f'Benchmark Return: +{bh_ret:.1f}% | Max DD: {bh_dd:.1f}%\n')

    # Train the ML Engine
    ml = MLEngine()
    ml.train_model(db, train_start='2018-01-01', train_end='2020-12-31')

    # To drop the MLEngine into the DecisionEngine, we must adapt the Entry rules.
    # The default DecisionEngine waits for an RSI/MACD hook. 
    # With ML, if the probability of a stock outperforming is > 55%, we just buy it.
    
    class MLDecisionEngine(DecisionEngine):
        def run_simulation(self, data_engine, macro_engine, rules_engine, start_date='2019-01-01'):
            spy = data_engine.daily['SPY']
            dates = spy[spy.index >= start_date].index
            
            print(f"Running ML Portfolio simulation over {len(dates)} days...")
            
            for i in range(2, len(dates)):
                today = dates[i]
                yesterday = dates[i-1]
                
                # Fetch Current Prices
                current_prices = {}
                for sym, df in data_engine.daily.items():
                    if today in df.index:
                        current_prices[sym] = df.loc[today, 'close']
                        
                port_val = self.get_portfolio_value(today, current_prices)
                self.equity_curve.append({'date': today, 'equity': port_val})
                
                # 1. Macro Weather Check
                weather = macro_engine.get_weather(yesterday)
                
                # 2. Manage Exits and Dynamic Stops
                symbols_to_remove = []
                for sym, pos in self.positions.items():
                    if sym not in data_engine.daily: continue
                    df = data_engine.daily[sym]
                    if today not in df.index: continue
                    
                    today_row = df.loc[today]
                    
                    if today_row['close'] > pos['highest']:
                        pos['highest'] = today_row['close']
                        atr_val = today_row['atr_14'] if not pd.isna(today_row['atr_14']) else today_row['close']*0.02
                        pos['stop'] = pos['highest'] - (atr_val * self.atr_stop)
                    
                    exit_triggered = False
                    reason = ""
                    
                    if not weather:
                        exit_triggered = True; reason = "Macro Bear"
                    elif today_row['close'] < pos['stop']:
                        exit_triggered = True; reason = "Trailing Stop"
                        
                    if exit_triggered:
                        exit_price = today_row['close']
                        self.capital += pos['shares'] * exit_price
                        self.history.append({
                            'symbol': sym, 'entry_date': pos['entry_date'], 'exit_date': today,
                            'entry_price': pos['entry'], 'exit_price': exit_price,
                            'shares': pos['shares'],
                            'pnl_pct': (exit_price - pos['entry'])/pos['entry']*100,
                            'reason': reason
                        })
                        symbols_to_remove.append(sym)
                        
                for sym in symbols_to_remove:
                    del self.positions[sym]
                    
                # 3. New ML Entry Execution
                if weather and len(self.positions) < self.max_positions:
                    # ML Engine scores all symbols for Probability of Outperformance
                    leaderboard = rules_engine.score_symbols(yesterday, data_engine)
                    if leaderboard.empty: continue
                    
                    candidates = leaderboard[~leaderboard['symbol'].isin(self.positions.keys())]
                    
                    for _, row in candidates.iterrows():
                        if len(self.positions) >= self.max_positions: break
                        
                        sym = row['symbol']
                        prob = row['score']
                        
                        # ML Signal: If AI predicts a > 53% chance of generating alpha over SPY, we enter immediately. 
                        # This skips rigid RSI hooks.
                        if prob > 0.53:
                            df = data_engine.daily[sym]
                            if today not in df.index or yesterday not in df.index: continue
                            
                            entry_price = df.loc[today, 'open']
                            yest_row = df.loc[yesterday]
                            atr_val = yest_row['atr_14'] if not pd.isna(yest_row['atr_14']) else entry_price*0.02
                            
                            risk_dollars = port_val * self.risk_pct
                            stop_dist = atr_val * self.atr_stop
                            shares = int(risk_dollars / stop_dist) if stop_dist > 0 else 0
                            
                            cost = shares * entry_price
                            if cost > self.capital:
                                shares = int(self.capital // entry_price)
                                cost = shares * entry_price
                                
                            if shares > 0:
                                self.capital -= cost
                                self.positions[sym] = {
                                    'shares': shares, 'entry': entry_price, 'entry_date': today,
                                    'highest': entry_price, 'stop': entry_price - (atr_val * self.atr_stop)
                                }
                                
            # End loop closing
            if len(self.positions) > 0:
                last_date = dates[-1]
                for sym, pos in list(self.positions.items()):
                    if sym in data_engine.daily and last_date in data_engine.daily[sym].index:
                        exit_price = data_engine.daily[sym].loc[last_date, 'close']
                        self.capital += pos['shares'] * exit_price
                        self.history.append({
                            'symbol': sym, 'entry_date': pos['entry_date'], 'exit_date': last_date,
                            'entry_price': pos['entry'], 'exit_price': exit_price,
                            'shares': pos['shares'],
                            'pnl_pct': (exit_price - pos['entry'])/pos['entry']*100,
                            'reason': 'End of Data'
                        })
                        del self.positions[sym]
                        
            return pd.DataFrame(self.history), pd.DataFrame(self.equity_curve)

    macro = MacroEngine(db)
    # Using stricter ML probability (54% outperformance likelihood threshold) and rotating capital
    brain = MLDecisionEngine(initial_capital=100000, max_positions=5, atr_stop=3.0, risk_pct=0.15)
    
    trades, equity = brain.run_simulation(db, macro, ml, start_date='2021-01-01')

    ret = (equity.iloc[-1]['equity'] - 100000) / 100000 * 100
    wins = trades[trades['pnl_pct'] > 0]
    wr = len(wins) / len(trades) * 100 if len(trades) > 0 else 0
    equity['peak'] = equity['equity'].cummax()
    dd = (equity['peak'] - equity['equity']) / equity['peak'] * 100
    
    print(f'\\n--- FINAL ML ENGINE RESULTS ---')
    print(f'AI Portfolio Return: {ret:+.1f}% (vs B&H {bh_ret:+.1f}%)')
    print(f'AI Max Drawdown: {dd.max():.1f}% (vs B&H {bh_dd:.1f}%)')
    print(f'Total Trades: {len(trades)} | Win Rate: {wr:.1f}%')
    print(f'Average Trade PNL: {trades["pnl_pct"].mean():.1f}%')

if __name__ == '__main__':
    run_ml_backtest()
