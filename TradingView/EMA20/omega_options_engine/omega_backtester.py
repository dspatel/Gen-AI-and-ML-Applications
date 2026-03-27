import logging
import json
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import requests
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from omega_data_pipeline import OmegaDataPipeline
from omega_actor import OmegaBaselineActor
from omega_universe import OmegaUniverse
from world_state_logger import OptionsWorldStateLogger

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaHistoricalBacktester:
    """
    The "Time Machine" module.
    Replays historical 15m stock bars to evaluate the Baseline Actor's ORB 
    and Greeks-driven Logic against 'blind' past data.
    Requires Alpaca Historical Options Data access for true PnL fidelity.
    """
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
        
        self.api_key, self.api_secret = self._load_credentials(config_path)
        self.data_url = "https://data.alpaca.markets"
        
        self.universe = OmegaUniverse()
        self.pipeline = OmegaDataPipeline()
        # Initialize the Baseline Actor to use its pure math evaluation functions
        self.actor_brain = OmegaBaselineActor()
        
        # We write backtest results to a separate DB to avoid corrupting the live trainer
        self.backtest_db = OptionsWorldStateLogger(db_dir=os.path.dirname(__file__))
        self.backtest_db.db_path = os.path.join(os.path.dirname(__file__), "omega_backtest_results.db")
        self.backtest_db._initialize_db()

    def _load_credentials(self, config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            # Use the Paper Account for options data fetching
            acct = next((a for a in config['accounts'] if 'Paper' in a['name']), config['accounts'][0])
            logger.info(f"Backtester loaded Alpaca credentials: {acct['name']}")
            return acct['key'], acct['secret']
        except Exception as e:
            logger.error(f"Failed to load Alpaca credentials: {e}")
            return None, None

    def fetch_historical_stock_bars(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetches historic 15Min bars for the underlying to simulate intraday flow.
        """
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json"
        }
        
        url = f"{self.data_url}/v2/stocks/bars?symbols={ticker}&timeframe=15Min&start={start_date}T09:30:00Z&end={end_date}T16:00:00Z&limit=1000&adjustment=raw"
        
        try:
            logger.info(f"Fetching historical 15m stock bars for {ticker} from {start_date} to {end_date}...")
            all_bars = []
            page_token = None
            while True:
                req_url = url
                if page_token:
                    req_url += f"&page_token={page_token}"
                response = requests.get(req_url, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    bars = data.get('bars', {}).get(ticker, [])
                    if not bars and not all_bars:
                        logger.warning(f"No historical bar data returned for {ticker}.")
                        return pd.DataFrame()
                    all_bars.extend(bars)
                    
                    page_token = data.get('next_page_token')
                    if not page_token:
                        break
                else:
                    logger.error(f"Alpaca Bar Data Error {response.status_code}: {response.text}")
                    break
                    
            if not all_bars: return pd.DataFrame()
            df = pd.DataFrame(all_bars)
            # Map Alpaca dict keys (t, o, h, l, c, v) to readable names
            df.rename(columns={'t': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'}, inplace=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            return df
        except Exception as e:
            logger.error(f"Exception fetching historic stock data: {e}")
            return pd.DataFrame()

    def backtest_symbol(self, ticker: str, start_date: str, end_date: str):
        """
        Runs the simulation loop over the historical timeframe.
        """
        # 1. Fetch the underlying price action "tape"
        tape_df = self.fetch_historical_stock_bars(ticker, start_date, end_date)
        if tape_df.empty:
            return
            
        logger.info(f"Loaded {len(tape_df)} 15-minute historical bars for {ticker}.")
        
        # State Tracking
        in_trade = False
        current_contract = None
        current_action = 0
        simulated_bucket = "B"
        # Daily State Tracking
        current_date = None
        
        # Performance Tracking
        completed_trades = []
        
        # Calculate Technical Indicators for Volatility and Mean Reversion
        
        # 1. BBW (Bollinger Band Width)
        tape_df['SMA20'] = tape_df['close'].rolling(window=20).mean()
        tape_df['STD20'] = tape_df['close'].rolling(window=20).std()
        tape_df['BB_Upper'] = tape_df['SMA20'] + (tape_df['STD20'] * 2)
        tape_df['BB_Lower'] = tape_df['SMA20'] - (tape_df['STD20'] * 2)
        tape_df['BBW'] = (tape_df['BB_Upper'] - tape_df['BB_Lower']) / tape_df['SMA20']
        
        # 2. RSI (14-period)
        delta = tape_df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        tape_df['RSI'] = 100 - (100 / (1 + rs))
        
        # 3. Proxy for IV Rank (using Historical Volatility Rank)
        tape_df['HV'] = tape_df['close'].pct_change().rolling(window=20).std() * (252**0.5) * 100
        min_hv = tape_df['HV'].rolling(window=252).min() # Approximation using 15m bars, but fine for backtest mechanics
        max_hv = tape_df['HV'].rolling(window=252).max()
        tape_df['IV_RANK'] = ((tape_df['HV'] - min_hv) / (max_hv - min_hv)) * 100
        tape_df['IV_RANK'] = tape_df['IV_RANK'].fillna(50.0)
        
        # 4. Fetch Historical VIX to determine Volatility Momentum (Phase 10 Drawdown Mitigation)
        logger.info(f"Fetching Volatility Regime data (^VIX)...")
        vix_df = self.fetch_historical_stock_bars("SPY", start_date, end_date) # Alpaca doesn't easily serve ^VIX on lower tiers.
        # FIX: For simulation purposes, we will synthesize VIX momentum from the SPY ATR to ensure it runs without premium data feeds.
        # A true production build uses CBOE VIX data.
        tape_df['ATR_14'] = tape_df['high'] - tape_df['low']
        tape_df['ATR_14'] = tape_df['ATR_14'].rolling(window=14).mean()
        tape_df['ATR_EMA10'] = tape_df['ATR_14'].ewm(span=10, adjust=False).mean()
        # If current volatility (ATR) is higher than its 10-period trend, volatility is expanding (positive momentum)
        tape_df['VOL_MOMENTUM'] = tape_df['ATR_14'] > tape_df['ATR_EMA10']

        for index, row in tape_df.iterrows():
            current_time = row['timestamp']
            current_price = row['close']
            rsi = row['RSI'] if pd.notna(row['RSI']) else 50.0
            bbw = row['BBW'] if pd.notna(row['BBW']) else 0.10
            iv_rank = row['IV_RANK']
            vix_momentum = row['VOL_MOMENTUM']
            
            # Simulated End of Day Check
            if current_time.hour >= 15 and current_time.minute >= 45:
                if in_trade:
                    logger.info(f"[{current_time}] EOD THETA STOP: Liquidating {current_contract} at market close.")
                    in_trade = False
                continue # Skip new setups last 15 mins

            # --- Check For Exits ---
            if in_trade:
                # Calculate metrics for exit evaluation
                time_held_hours = (current_time - entry_time).total_seconds() / 3600.0
                
                # Phase 25B: Dynamic Greek Approximation
                if simulated_bucket == "A":
                    gamma_mult = 12.0
                    theta_bleed = 0.8
                elif simulated_bucket == "B":
                    gamma_mult = 8.0
                    theta_bleed = 0.5
                else: # Bucket C
                    gamma_mult = 4.0
                    theta_bleed = 0.2
                    
                # PnL Approximations for VRP strategies
                if current_action == 1: # Long Straddle (Requires massive move, Max Loss -100%)
                    move_pct = abs((current_price - entry_price) / entry_price) * 100
                    approx_option_return_pct = (move_pct * gamma_mult) - (time_held_hours * theta_bleed) 
                    approx_option_return_pct = max(-100.0, approx_option_return_pct)
                elif current_action == 2: # Short Iron Condor (Requires low movement)
                    move_pct = abs((current_price - entry_price) / entry_price) * 100
                    approx_option_return_pct = (time_held_hours * (theta_bleed * 3.0)) - (move_pct * (gamma_mult * 0.6)) 
                    approx_option_return_pct = max(-300.0, min(100.0, approx_option_return_pct)) # Capped defined risk
                elif current_action in [3, 4]: # Credit Spreads
                    move_pct = ((current_price - entry_price) / entry_price) * 100
                    if current_action == 3: # Call Credit Spread (Bearish)
                        move_pct = -move_pct 
                    approx_option_return_pct = (time_held_hours * (theta_bleed * 2.0)) + (move_pct * (gamma_mult * 0.5)) 
                    approx_option_return_pct = max(-200.0, min(100.0, approx_option_return_pct)) # Defined risk spread
                else:
                    approx_option_return_pct = 0.0
                    
                # Phase 24: Hard -20% Stop Loss Guardrail (Absolute Override)
                if approx_option_return_pct <= -20.0:
                    exit_triggered = True
                    exit_reason = "Phase 24: Hard -20% Vega Stop"
                else:
                    # Use the Actor's actual brain to evaluate the exit
                    if current_action == 1: pos_type = 'straddle'
                    elif current_action == 2: pos_type = 'iron_condor'
                    else: pos_type = 'credit_spread'
                    
                    self.actor_brain.active_simulated_positions = {ticker: {'type': pos_type, 'contract': current_contract}}
                    exit_triggered, exit_reason = self.actor_brain.evaluate_exit_trail(
                        ticker, time_held_hours, approx_option_return_pct
                    )
                
                if exit_triggered:
                    logger.info(f"[{current_time}] EXIT TRIGGERED [{exit_reason}]: Sold {current_contract}. Approx option return: {approx_option_return_pct:.2f}%")
                    
                    completed_trades.append({
                        'entry_time': entry_time,
                        'exit_time': current_time,
                        'contract': current_contract,
                        'reason': exit_reason,
                        'return_pct': approx_option_return_pct
                    })
                    
                    in_trade = False
                    self.actor_brain.active_simulated_positions = {}
                    
            # --- Check For Entries ---
            elif not in_trade:
                # PHASE 24: Hard IV Peak Rejection Filter (> 80th Percentile)
                if iv_rank > 80.0:
                    continue
                    
                action = self.actor_brain.evaluate_entry_vrp(ticker, iv_rank, bbw, rsi, vix_momentum)
                
                if action != 0:
                    # Phase 25B: Store DTE Bucket Data inside the simulator
                    if iv_rank > 60.0:
                        simulated_bucket = "C"
                    elif bbw < 0.03:
                        simulated_bucket = "A"
                    else:
                        simulated_bucket = "B"
                        
                    contract_suffix = (current_time + timedelta(days=30)).strftime("%y%m%d")
                    strike_str = f"{int(current_price * 1000):08d}"
                    current_contract = f"{ticker}{contract_suffix}_VRP_{action}_{strike_str}"
                    current_action = action
                    
                    if action == 1: strat_name = "Long Straddle"
                    elif action == 2: strat_name = "Short Iron Condor"
                    elif action == 3: strat_name = "Short Call Spread"
                    else: strat_name = "Short Put Spread"
                    
                    logger.info(f"[{current_time}] VRP TRIGGER (IV: {iv_rank:.1f}, BBW: {bbw:.3f}, RSI: {rsi:.1f}): Entering {strat_name} | Underlying: ${current_price:.2f}")
                    in_trade = True
                    entry_price = current_price
                    entry_time = current_time

        # Generate True Statistics from the loop
        self._print_statistics(ticker, completed_trades)

    def _calculate_pnl(self, current_action, simulated_bucket, current_price, entry_price, time_held_hours):
        """Helper to isolate PnL math for reuse in the portfolio loop."""
        if simulated_bucket == "A":
            gamma_mult, theta_bleed = 12.0, 0.8
        elif simulated_bucket == "B":
            gamma_mult, theta_bleed = 8.0, 0.5
        else:
            gamma_mult, theta_bleed = 4.0, 0.2
            
        if current_action == 1:
            move_pct = abs((current_price - entry_price) / entry_price) * 100
            ret = (move_pct * gamma_mult) - (time_held_hours * theta_bleed) 
            return max(-100.0, ret)
        elif current_action == 2:
            move_pct = abs((current_price - entry_price) / entry_price) * 100
            ret = (time_held_hours * (theta_bleed * 3.0)) - (move_pct * (gamma_mult * 0.6)) 
            return max(-300.0, min(100.0, ret))
        elif current_action in [3, 4]:
            move_pct = ((current_price - entry_price) / entry_price) * 100
            if current_action == 3: move_pct = -move_pct 
            ret = (time_held_hours * (theta_bleed * 2.0)) + (move_pct * (gamma_mult * 0.5)) 
            return max(-200.0, min(100.0, ret))
        return 0.0

    def backtest_portfolio(self, tickers: list, start_date: str, end_date: str):
        """
        Chronologically aligns all datasets to simulate the "Hunger Games" 
        global capital constraint and edge_score ranking limits of the Live Engine.
        """
        logger.info(f"Loading matrices for {len(tickers)} symbols... this may take a moment.")
        tapes = {}
        for ticker in tickers:
            df = self.fetch_historical_stock_bars(ticker, start_date, end_date)
            if df.empty: continue
            
            # Recreate indicators mathematically (identical to isolated loop)
            df['SMA20'] = df['close'].rolling(window=20).mean()
            df['STD20'] = df['close'].rolling(window=20).std()
            df['BBW'] = ((df['SMA20'] + (df['STD20'] * 2)) - (df['SMA20'] - (df['STD20'] * 2))) / df['SMA20']
            
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            df['RSI'] = 100 - (100 / (1 + (gain / loss)))
            
            df['HV'] = df['close'].pct_change().rolling(window=20).std() * (252**0.5) * 100
            df['IV_RANK'] = ((df['HV'] - df['HV'].rolling(window=252).min()) / (df['HV'].rolling(window=252).max() - df['HV'].rolling(window=252).min())) * 100
            df['IV_RANK'] = df['IV_RANK'].fillna(50.0)
            
            df['ATR_14'] = (df['high'] - df['low']).rolling(window=14).mean()
            df['VOL_MOMENTUM'] = df['ATR_14'] > df['ATR_14'].ewm(span=10, adjust=False).mean()
            
            df.set_index('timestamp', inplace=True)
            df = df.dropna(subset=['IV_RANK', 'RSI', 'BBW', 'VOL_MOMENTUM'])
            tapes[ticker] = df
            
        if "SPY" not in tapes: 
            logger.error("SPY must be in universe to anchor timeline.")
            return
            
        timeline = tapes["SPY"].index.sort_values()
        portfolio = {}
        completed_trades = []
        
        logger.info(f"Chronological Matrix Synced. Running {len(timeline)} chronological frames (Limit: 5 Active Slots)...")
        
        for current_time in timeline:
            # 1. End of Day Guillotine (3:45 PM)
            if current_time.hour >= 15 and current_time.minute >= 45:
                for t in list(portfolio.keys()):
                    if t in tapes and current_time in tapes[t].index:
                        pos = portfolio[t]
                        current_price = tapes[t].loc[current_time, 'close']
                        time_held = (current_time - pos['entry_time']).total_seconds() / 3600.0
                        ret = self._calculate_pnl(pos['action'], pos['bucket'], current_price, pos['entry_price'], time_held)
                        
                        completed_trades.append({
                            'ticker': t,
                            'entry_time': pos['entry_time'],
                            'exit_time': current_time,
                            'reason': "EOD GUILLOTINE",
                            'return_pct': ret
                        })
                portfolio.clear()
                continue
                
            # 2. Check Exits Intraday
            for t in list(portfolio.keys()):
                pos = portfolio[t]
                if t not in tapes or current_time not in tapes[t].index: continue
                current_price = tapes[t].loc[current_time, 'close']
                time_held = (current_time - pos['entry_time']).total_seconds() / 3600.0
                ret = self._calculate_pnl(pos['action'], pos['bucket'], current_price, pos['entry_price'], time_held)
                
                exit_triggered = False
                exit_reason = ""
                
                if ret <= -20.0:
                    exit_triggered, exit_reason = True, "Max Loss -20%"
                elif ret >= 25.0:
                    exit_triggered, exit_reason = True, "Target Hit +25%"
                elif pos['action'] == 1 and time_held >= 2.5 and ret < 5.0:
                    exit_triggered, exit_reason = True, "Theta Time Hook"
                    
                if exit_triggered:
                    completed_trades.append({
                        'ticker': t,
                        'entry_time': pos['entry_time'],
                        'exit_time': current_time,
                        'reason': exit_reason,
                        'return_pct': ret
                    })
                    portfolio.pop(t)
            
            # 3. Capital Constrained Entries & Dynamic Rotation
            signals = []
            for t in tickers:
                if t in portfolio or t not in tapes or current_time not in tapes[t].index: continue
                row = tapes[t].loc[current_time]
                
                if row['IV_RANK'] > 80.0: continue
                
                action = self.actor_brain.evaluate_entry_vrp(t, float(row['IV_RANK']), float(row['BBW']), float(row['RSI']), bool(row['VOL_MOMENTUM']))
                if action != 0:
                    iv_rank, bbw, rsi = float(row['IV_RANK']), float(row['BBW']), float(row['RSI'])
                    if action == 1: edge_score = (100.0 - iv_rank) + (1.0 / max(bbw, 0.01))
                    elif action == 2: edge_score = iv_rank + bbw
                    else: edge_score = abs(50.0 - rsi)
                    
                    signals.append({'ticker': t, 'action': action, 'price': float(row['close']), 'edge': edge_score, 'iv': iv_rank, 'bbw': bbw})
            
            if signals:
                signals.sort(key=lambda x: x['edge'], reverse=True)
                
                open_slots = 5 - len(portfolio)
                
                # --- Dynamic Portfolio Rotation Matrix ---
                # If portfolio is full, we audit the mathematical edge of our CURRENT holdings.
                # If the incoming best candidate is > 3x superior to our weakest holding, we liquidate the weak link.
                if open_slots == 0 and len(portfolio) == 5:
                    weakest_ticker = None
                    weakest_score = 999999.0
                    
                    for pt in list(portfolio.keys()):
                        if pt not in tapes or current_time not in tapes[pt].index: continue
                        pt_row = tapes[pt].loc[current_time]
                        
                        pos_action = portfolio[pt]['action']
                        p_iv, p_bbw, p_rsi = float(pt_row['IV_RANK']), float(pt_row['BBW']), float(pt_row['RSI'])
                        
                        if pos_action == 1: p_edge = (100.0 - p_iv) + (1.0 / max(p_bbw, 0.01))
                        elif pos_action == 2: p_edge = p_iv + p_bbw
                        else: p_edge = abs(50.0 - p_rsi)
                        
                        if p_edge < weakest_score:
                            weakest_score = p_edge
                            weakest_ticker = pt
                            
                    best_new_signal = signals[0]
                    # The 3x Multiplier Rule to prevent high-frequency slippage whip-sawing
                    if weakest_ticker is not None and best_new_signal['edge'] >= (weakest_score * 3.0):
                        logger.debug(f"DYNAMIC ROTATION: Liquidating weak {weakest_ticker} (Edge {weakest_score:.1f}) for strong {best_new_signal['ticker']} (Edge {best_new_signal['edge']:.1f})")
                        pos = portfolio[weakest_ticker]
                        current_price = tapes[weakest_ticker].loc[current_time, 'close']
                        time_held = (current_time - pos['entry_time']).total_seconds() / 3600.0
                        ret = self._calculate_pnl(pos['action'], pos['bucket'], current_price, pos['entry_price'], time_held)
                        
                        completed_trades.append({
                            'ticker': weakest_ticker,
                            'entry_time': pos['entry_time'],
                            'exit_time': current_time,
                            'reason': "DYNAMIC ROTATION LIQUIDATION",
                            'return_pct': ret
                        })
                        portfolio.pop(weakest_ticker)
                        open_slots = 1 # Successfully created space for the superior Alpha
                
                # Fill open slots (Organically empty or freed via Rotation)
                if open_slots > 0:
                    for sig in signals[:open_slots]:
                        if sig['iv'] > 60.0: bucket = "C"
                        elif sig['bbw'] < 0.03: bucket = "A"
                        else: bucket = "B"
                        
                        portfolio[sig['ticker']] = {
                            'entry_time': current_time,
                            'entry_price': sig['price'],
                            'action': sig['action'],
                            'bucket': bucket
                        }
                        
        self._print_statistics("UNIFIED PORTFOLIO (CAPITAL CONSTRAINED)", completed_trades)

    def _print_statistics(self, ticker, trades):
        logger.info("\n==================================================")
        logger.info(f"    BLIND DATA OPTIONS PERFORMANCE ({ticker})     ")
        logger.info("==================================================")
        
        if not trades:
            logger.info("No trades were executed during the backtest timeframe.")
            return
            
        total_trades = len(trades)
        winning_trades = [t for t in trades if t['return_pct'] > 0]
        losing_trades = [t for t in trades if t['return_pct'] <= 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
        avg_win = sum([t['return_pct'] for t in winning_trades]) / len(winning_trades) if winning_trades else 0
        avg_loss = sum([t['return_pct'] for t in losing_trades]) / len(losing_trades) if losing_trades else 0
        
        cumulative_return = sum([t['return_pct'] for t in trades])
        
        # Calculate Average Daily Return
        unique_days = len(set([t['exit_time'].date() for t in trades]))
        avg_daily_return = cumulative_return / unique_days if unique_days > 0 else 0.0
        
        reasons = {}
        for t in trades:
            reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
            
        logger.info(f"Total Simulated Trades:    {total_trades}")
        logger.info(f"Win Rate:                  {win_rate:.2f}%")
        logger.info(f"Average Winning Trade:     +{avg_win:.2f}%")
        logger.info(f"Average Losing Trade:      {avg_loss:.2f}%")
        logger.info(f"Cumulative Options PnL:    {cumulative_return:.2f}%")
        logger.info(f"Average Daily Return (ADR): {avg_daily_return:.2f}%")
        
        logger.info("\n--- EXIT REASON BREAKDOWN ---")
        for reason, count in reasons.items():
            logger.info(f"{reason}: {count} trades")
        logger.info("==================================================\n")

if __name__ == "__main__":
    backtester = OmegaHistoricalBacktester()
    
    etfs = ["SPY", "QQQ", "IWM"]
    equities = ["META", "TSLA", "GOOGL", "AMD", "NVDA"]
    
    # User requested strict blind testing on dynamic rotation across 2024 and 2025
    for target_year in [2024, 2025]:
        start_str = f"{target_year}-01-01"
        end_str = f"{target_year}-12-31"
        
        logger.info(f"--- INITIATING {target_year} OMEGA DYNAMIC ROTATION BACKTEST ---")
        
        # Combined universe to force the 5-slot ranking limit and Dynamic Rotation into action
        backtester.backtest_portfolio(etfs + equities, start_str, end_str)

    logger.info("--- MULTI-YEAR COMPARATIVE BACKTEST COMPLETE ---")
