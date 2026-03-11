"""
The Decision Engine (Portfolio Brain)
=====================================
Integrates the Signal Engines, manages a simulated $100k portfolio, 
and executes Volatility Parity capital allocation across the 16-symbol pool.
"""
import pandas as pd
import numpy as np
from signal_engines import PortfolioDataEngine, MacroEngine, RulesEngine

class DecisionEngine:
    def __init__(self, initial_capital=100000, max_positions=5, rsi_entry=40, atr_stop=3.0, risk_pct=0.08):
        self.capital = initial_capital
        self.max_positions = max_positions
        self.rsi_entry = rsi_entry
        self.atr_stop = atr_stop
        self.risk_pct = risk_pct # Risk 8% of total portfolio equity per trade (Aggressive)
        
        self.positions = {} # {symbol: {'shares': x, 'entry': y, 'highest': z, 'stop': s}}
        self.history = []
        self.equity_curve = []
        
    def get_portfolio_value(self, date_str, current_prices):
        val = self.capital
        for sym, pos in self.positions.items():
            if sym in current_prices:
                val += pos['shares'] * current_prices[sym]
        return val

    def run_simulation(self, data_engine, macro_engine, rules_engine, start_date='2019-01-01'):
        # Get common dates from the SPY benchmark
        spy = data_engine.daily['SPY']
        dates = spy[spy.index >= start_date].index
        
        print(f"Running simulation over {len(dates)} days...")
        
        for i in range(2, len(dates)):
            today = dates[i]
            yesterday = dates[i-1]
            day_before = dates[i-2]
            
            # Extract today's prices for portfolio valuation
            current_prices = {}
            for sym, df in data_engine.daily.items():
                if today in df.index:
                    current_prices[sym] = df.loc[today, 'close']
                    
            port_val = self.get_portfolio_value(today, current_prices)
            self.equity_curve.append({'date': today, 'equity': port_val})
            
            # 1. Macro Weather Check
            weather = macro_engine.get_weather(yesterday)
            
            # 2. Manage Existing Positions
            symbols_to_remove = []
            for sym, pos in self.positions.items():
                if sym not in data_engine.daily: continue
                df = data_engine.daily[sym]
                if today not in df.index: continue
                
                today_row = df.loc[today]
                
                # Update trailing stop
                if today_row['close'] > pos['highest']:
                    pos['highest'] = today_row['close']
                    atr_val = today_row['atr_14'] if not pd.isna(today_row['atr_14']) else today_row['close']*0.02
                    pos['stop'] = pos['highest'] - (atr_val * self.atr_stop)
                
                exit_triggered = False
                exit_reason = ""
                
                # Exit 1: Weather Turned Bearish (Liquidate Everything)
                if not weather:
                    exit_triggered = True
                    exit_reason = "Macro Bear Crash"
                    
                # Exit 2: Trailing Stop Hit
                elif today_row['close'] < pos['stop']:
                    exit_triggered = True
                    exit_reason = "Trailing Stop"
                    
                if exit_triggered:
                    exit_price = today_row['close']
                    revenue = pos['shares'] * exit_price
                    self.capital += revenue
                    
                    self.history.append({
                        'symbol': sym, 'entry_date': pos['entry_date'], 'exit_date': today,
                        'entry_price': pos['entry'], 'exit_price': exit_price,
                        'shares': pos['shares'],
                        'pnl_pct': (exit_price - pos['entry'])/pos['entry']*100,
                        'reason': exit_reason
                    })
                    symbols_to_remove.append(sym)
                    
            for sym in symbols_to_remove:
                del self.positions[sym]
                
            # 3. New Entry Searching
            if weather and len(self.positions) < self.max_positions:
                # Rank the pool
                leaderboard = rules_engine.score_symbols(yesterday)
                if leaderboard.empty: continue
                
                # Filter to top candidates not already in portfolio
                candidates = leaderboard[leaderboard['score'] != -999]
                candidates = candidates[~candidates['symbol'].isin(self.positions.keys())]
                
                # Check momentum entry triggers on the leaders
                for _, row in candidates.iterrows():
                    if len(self.positions) >= self.max_positions: break
                    
                    sym = row['symbol']
                    df = data_engine.daily[sym]
                    if yesterday not in df.index or day_before not in df.index: continue
                    
                    yest_row = df.loc[yesterday]
                    db_row = df.loc[day_before]
                    
                    # Entry Trigger: RSI Hook Up from oversold
                    rsi_hook = (yest_row['rsi_14'] > self.rsi_entry and db_row['rsi_14'] <= self.rsi_entry)
                    macd_cross = (yest_row['macd_hist'] > 0 and db_row['macd_hist'] <= 0)
                    
                    if rsi_hook or macd_cross:
                        if today not in df.index: continue
                        entry_price = df.loc[today, 'open']
                        atr_val = yest_row['atr_14'] if not pd.isna(yest_row['atr_14']) else entry_price*0.02
                        
                        # --- Volatility Parity Sizing ---
                        # Risk $ amount based on % of portfolio equity
                        risk_dollars = port_val * self.risk_pct
                        
                        # Distance to stop loss
                        stop_dist = atr_val * self.atr_stop
                        
                        # Shares = Risk $ / Stop Distance
                        shares = int(risk_dollars / stop_dist) if stop_dist > 0 else 0
                        
                        # Ensure we don't spend more than available cash
                        cost = shares * entry_price
                        if cost > self.capital:
                            shares = int(self.capital // entry_price)
                            cost = shares * entry_price
                            
                        if shares > 0:
                            self.capital -= cost
                            self.positions[sym] = {
                                'shares': shares,
                                'entry': entry_price,
                                'entry_date': today,
                                'highest': entry_price,
                                'stop': entry_price - (atr_val * self.atr_stop)
                            }
                            
        # Close out remaining at end for metrics
        if len(self.positions) > 0:
            last_date = dates[-1]
            last_prices = {}
            for sym, df in data_engine.daily.items():
                if last_date in df.index:
                    last_prices[sym] = df.loc[last_date, 'close']
                    
            for sym, pos in list(self.positions.items()):
                if sym in last_prices:
                    exit_price = last_prices[sym]
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

if __name__ == "__main__":
    db = PortfolioDataEngine()
    db.load_all_data()
    
    macro = MacroEngine(db)
    
    # Need to slightly modify RulesEngine to accept db as param in score_symbols
    class InteractiveRulesEngine(RulesEngine):
        def score_symbols(self, current_date, db_engine):
            self.data_engine = db_engine
            return super().score_symbols(current_date)
            
    rules = InteractiveRulesEngine(db)
    
    brain = DecisionEngine()
    trades, equity = brain.run_simulation(db, macro, rules, start_date='2021-01-01')
    
    print("\n--- Decision Engine Results ---")
    ret = (equity.iloc[-1]['equity'] - 100000) / 100000 * 100
    print(f"Total Strategy Return: {ret:+.1f}%")
    
    wins = trades[trades['pnl_pct'] > 0]
    wr = len(wins) / len(trades) * 100 if len(trades) > 0 else 0
    print(f"Total Trades: {len(trades)} | Win Rate: {wr:.1f}%")
    
    equity['peak'] = equity['equity'].cummax()
    equity['dd'] = (equity['peak'] - equity['equity']) / equity['peak'] * 100
    print(f"Max Drawdown: {equity['dd'].max():.1f}%")
