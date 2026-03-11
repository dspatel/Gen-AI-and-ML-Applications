import pandas as pd
import numpy as np
from signal_engines import PortfolioDataEngine, MacroEngine, RulesEngine
from sentiment_engine import SentimentEngine
from earnings_engine import EarningsEngine
from decision_engine import DecisionEngine

def run_rotation_backtest():
    db = PortfolioDataEngine()
    db.load_all_data()

    print('Calculating 16-Symbol Equal Weight Benchmark (2021 - 2026)...')
    symbols = [s for s in db.daily.keys() if s != 'SPY']
    start_date = '2021-01-01'
    starting_cap = 100000 / len(symbols)

    bh_equity = pd.Series(0.0, index=db.daily['QQQ'][db.daily['QQQ'].index >= start_date].index if 'QQQ' in db.daily else db.daily['SPY'][db.daily['SPY'].index >= start_date].index)
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

    # ========================================================================
    # The NLP Sentiment Engine (GPU-Accelerated FinBERT on RTX 4090)
    # ========================================================================
    nlp = SentimentEngine()
    nlp.load_sentiment_data()
    
    # ========================================================================
    # The Earnings Engine (Historical EPS Surprise + Calendar Awareness)
    # ========================================================================
    earnings = EarningsEngine()
    earnings.load_sentiment_data()

    class RotationDecisionManager(DecisionEngine):
        def run_simulation(self, data_engine, macro_engine, rules_engine, nlp_engine, earnings_engine, start_date='2019-01-01'):
            spy = data_engine.daily['SPY']
            dates = spy[spy.index >= start_date].index
            
            print(f"Running Dynamic Alpha Rotation simulation over {len(dates)} days...")
            
            # ================================================================
            # VARIABLE PORTFOLIO SIZING: 2 to 10 dynamic positions
            # Expand during broad bull runs to capture massive sector breadth
            # Contract during mixed markets to concentrate in the few winners
            # ================================================================
            MAX_POSITIONS = 10   # Broad exposure ceiling
            MIN_POSITIONS = 2    # Always at least 2 for diversification
            BASE_RISK = 0.10     # 10% portfolio risk per position (scales dynamically)
            
            for i in range(2, len(dates)):
                today = dates[i]
                yesterday = dates[i-1]
                
                # Portfolio Valuation
                current_prices = {}
                for sym, df in data_engine.daily.items():
                    if today in df.index:
                        current_prices[sym] = df.loc[today, 'close']
                port_val = self.get_portfolio_value(today, current_prices)
                self.equity_curve.append({'date': today, 'equity': port_val})
                
                # 1. Macro Weather
                weather = macro_engine.get_weather(yesterday)
                
                # Update the Daily Leaderboard
                leaderboard = rules_engine.score_symbols(yesterday)
                if leaderboard.empty: continue
                
                # ============================================================
                # DYNAMIC POSITION COUNT (VARIABLE SIZING)
                # Count how many symbols pass the strict MTF entry filters:
                # Price > SMA 50 AND > EMA 20 AND > SMA 200
                # Target positions = number of passing symbols (capped 2 to 10)
                # ============================================================
                passing_symbols = 0
                for _, row in leaderboard.iterrows():
                    # MTF check: Ensure the indicators aren't missing and price is above all 3
                    if (pd.notna(row.get('sma_50')) and row['close'] > row['sma_50'] and
                        pd.notna(row.get('ema_20')) and row['close'] > row['ema_20'] and
                        pd.notna(row.get('sma_200')) and row['close'] > row['sma_200']):
                        passing_symbols += 1
                        
                target_positions = max(MIN_POSITIONS, min(MAX_POSITIONS, passing_symbols))
                
                # With a 48-symbol universe, we only want the absolute best.
                # If a stock falls out of the Top 10, it's dead money. Rotate it.
                elite_tier = leaderboard.head(10)['symbol'].tolist()
                
                # ============================================================
                # 2. EXIT MANAGEMENT: NLP-Adjusted Dynamic Trailing Stops
                # ============================================================
                symbols_to_remove = []
                for sym, pos in self.positions.items():
                    if sym not in data_engine.daily: continue
                    df = data_engine.daily[sym]
                    if today not in df.index: continue
                    
                    today_row = df.loc[today]
                    
                    # PRIMARY: Technical Trend Proxy
                    sentiment_score = 0.0
                    if yesterday in df.index:
                        yest_row = df.loc[yesterday]
                        if 'sma_50' in yest_row and 'ret_20d' in yest_row:
                            if pd.notna(yest_row['sma_50']) and pd.notna(yest_row['ret_20d']):
                                if today_row['close'] > yest_row['sma_50'] and yest_row['ret_20d'] > 0.05:
                                    sentiment_score = 0.8
                                elif today_row['close'] < yest_row['sma_50'] and yest_row['ret_20d'] < -0.10:
                                    sentiment_score = -0.5
                    
                    # Adaptive Stop-Loss Width (Phase 11)
                    # Use the VIX value retrieved by the RulesEngine to float the base width.
                    # Base VIX (15.0) = 2.0 ATR stop
                    # High VIX (30.0) = 4.0 ATR stop (widens to survive the chop)
                    # Low VIX (10.0) = ~1.3 ATR stop (tightens aggressively)
                    vix_val = yest_row.get('vix_level', 15.0)
                    if pd.isna(vix_val): vix_val = 15.0
                    adaptive_base_stop = self.atr_stop * (vix_val / 15.0)
                    adaptive_base_stop = max(1.5, min(adaptive_base_stop, 5.0)) # Hard floors
                    
                    # NLP-Modulated Stop Width:
                    # Give massive winners more room to breathe, cut losers tighter
                    if sentiment_score > 0.5:
                        active_stop_mult = adaptive_base_stop + 4.0   # Massive room for high-sent
                    elif sentiment_score < -0.3:
                        active_stop_mult = adaptive_base_stop - 1.0   # Tighten if sentiment cracks
                    else:
                        active_stop_mult = adaptive_base_stop         # Baseline adaptive stop: Get out fast on bad news

                    if today_row['close'] > pos['highest']:
                        pos['highest'] = today_row['close']
                        atr_val = today_row['atr_14'] if pd.notna(today_row.get('atr_14', np.nan)) else today_row['close']*0.02
                        pos['stop'] = pos['highest'] - (atr_val * active_stop_mult)
                    
                    exit_triggered = False
                    reason = ""
                    
                    if not weather and sentiment_score < 0.2:
                        exit_triggered = True; reason = "Macro Bear Crash"
                    elif today_row['close'] < pos['stop']:
                        exit_triggered = True; reason = "Dynamic NLP Trailing Stop"
                    elif sym not in elite_tier and sentiment_score < 0.3:
                        exit_triggered = True; reason = "Lost Relative Strength Rank"
                        
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
                    
                # ============================================================
                # 3. ENTRY MANAGEMENT: Dynamic Aggressive Leaderboard Entries
                # ============================================================
                if weather and len(self.positions) < target_positions:
                    
                    # Target the top N leaders
                    top_candidates = leaderboard.head(target_positions)
                    
                    for _, row in top_candidates.iterrows():
                        if len(self.positions) >= target_positions: break
                        sym = row['symbol']
                        
                        if sym not in self.positions:
                            df = data_engine.daily[sym]
                            if today not in df.index or yesterday not in df.index: continue
                            
                            entry_price = df.loc[today, 'open']
                            yest_row = df.loc[yesterday]
                            
                            # ------------------------------------------------
                            # HARD ENTRY GATING (MTF + Trend + Sentiment)
                            # ------------------------------------------------
                            if pd.isna(yest_row.get('sma_50')) or yest_row['close'] < yest_row['sma_50']: continue
                            if pd.isna(yest_row.get('ema_20')) or yest_row['close'] < yest_row['ema_20']: continue
                            if pd.isna(yest_row.get('sma_200')) or yest_row['close'] < yest_row['sma_200']: continue
                            
                            atr_val = yest_row['atr_14'] if pd.notna(yest_row.get('atr_14', np.nan)) else entry_price*0.02
                            
                            # ------------------------------------------------
                            # SENTIMENT for sizing: Tech Primary only
                            # ------------------------------------------------
                            sym_sentiment = 0.0
                            if 'sma_50' in yest_row and 'ret_20d' in yest_row:
                                if pd.notna(yest_row['sma_50']) and pd.notna(yest_row['ret_20d']):
                                    if yest_row['close'] > yest_row['sma_50'] and yest_row['ret_20d'] > 0.10:
                                        sym_sentiment = 0.7
                            
                            # (Entry sizing uses pure tech proxy only)
                            
                            # Dynamic risk sizing based on conviction
                            if sym_sentiment > 0.5:
                                risk_mult = 1.5   # High conviction: increase size 50%
                            elif sym_sentiment > 0.2:
                                risk_mult = 1.2   # Moderate conviction: slight overweight
                            elif sym_sentiment < -0.3:
                                risk_mult = 0.5   # Negative sentiment: underweight
                            else:
                                risk_mult = 1.0   # Neutral: standard sizing
                            
                            # Scale risk per position inversely with position count.
                            # If target_positions is 10, risk is 1 * BASE_RISK (10%).
                            # If target_positions is 2, risk is 5 * BASE_RISK (50%) to concentrate.
                            position_risk = BASE_RISK * risk_mult * (10.0 / target_positions)
                            risk_dollars = port_val * position_risk
                            
                            
                            # Calculate adaptive entry stop width
                            vix_val = yest_row.get('vix_level', 15.0)
                            if pd.isna(vix_val): vix_val = 15.0
                            adaptive_base_stop = self.atr_stop * (vix_val / 15.0)
                            adaptive_base_stop = max(1.5, min(adaptive_base_stop, 5.0))
                            
                            stop_dist = atr_val * adaptive_base_stop
                            shares = int(risk_dollars / stop_dist) if stop_dist > 0 else 0
                            cost = shares * entry_price
                            
                            # Allocate available cash aggressively
                            if cost > self.capital:
                                shares = int(self.capital // entry_price); cost = shares * entry_price
                                
                            if shares > 0:
                                self.capital -= cost
                                self.positions[sym] = {
                                    'shares': shares, 'entry': entry_price, 'entry_date': today,
                                    'highest': entry_price, 'stop': entry_price - stop_dist
                                }
                                
            # End loop: Close all remaining positions
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
    rules = RulesEngine(db)
    
    # Tighter base stop (2.0 ATR) but sentiment can widen it to 6.0 ATR on winners
    brain = RotationDecisionManager(initial_capital=100000, max_positions=3, atr_stop=2.0, risk_pct=0.20)
    
    # RUN FULL 10-YEAR ADAPTIVE TEST
    trades, equity = brain.run_simulation(db, macro, rules, nlp, earnings, start_date='2016-01-01')

    ret = (equity.iloc[-1]['equity'] - 100000) / 100000 * 100
    wins = trades[trades['pnl_pct'] > 0]
    wr = len(wins) / len(trades) * 100 if len(trades) > 0 else 0
    equity['peak'] = equity['equity'].cummax()
    dd = (equity['peak'] - equity['equity']) / equity['peak'] * 100
    
    # Annualized Return
    num_years = (equity.iloc[-1]['date'] - equity.iloc[0]['date']).days / 365.25
    annualized = ((1 + ret/100) ** (1/num_years) - 1) * 100

    print(f'\n--- DYNAMIC ALPHA ROTATION ENGINE ---')
    print(f'Engine Return: {ret:+.1f}% (vs B&H {bh_ret:+.1f}%)')
    print(f'Annualized Return: {annualized:+.1f}% (Target: 25.0%)')
    print(f'Engine Max Drawdown: {dd.max():.1f}% (vs B&H {bh_dd:.1f}%)')
    print(f'Total Trades: {len(trades)} | Win Rate: {wr:.1f}%')
    print(f'Average Trade PNL: {trades["pnl_pct"].mean():.1f}%')
    
    print("\nTop 5 Most Profitable Trades:")
    if not trades.empty:
        best_trades = trades.sort_values(by='pnl_pct', ascending=False).head(5)
        print(best_trades[['symbol', 'entry_date', 'exit_date', 'pnl_pct', 'reason']].to_string(index=False))

    print("\nTrade Reason Breakdown:")
    if not trades.empty:
        print(trades.groupby('reason')['pnl_pct'].agg(['count', 'mean']).to_string())

if __name__ == '__main__':
    run_rotation_backtest()
