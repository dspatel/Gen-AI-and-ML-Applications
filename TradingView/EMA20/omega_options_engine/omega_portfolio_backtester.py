import logging
import json
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import requests

from omega_actor import OmegaBaselineActor
from omega_universe import OmegaUniverse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaPortfolioBacktester:
    """
    Simulates the Live Execution constraints (e.g. Max 5 Positions) across the entire 
    10-asset universe chronologically to see how Ranking affects statistical edge.
    """
    def __init__(self):
        config_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
        self.api_key, self.api_secret = self._load_credentials(config_path)
        self.data_url = "https://data.alpaca.markets"
        
        self.universe = OmegaUniverse()
        self.actor_brain = OmegaBaselineActor()

    def _load_credentials(self, config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            acct = next((a for a in config['accounts'] if 'Paper' in a['name']), config['accounts'][0])
            return acct['key'], acct['secret']
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return None, None

    def fetch_historical_stock_bars(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json"
        }
        
        url = f"{self.data_url}/v2/stocks/bars?symbols={ticker}&timeframe=15Min&start={start_date}T09:30:00Z&end={end_date}T16:00:00Z&limit=10000&adjustment=raw"
        
        all_bars = []
        page_token = None
        
        try:
            while True:
                req_url = url
                if page_token:
                    req_url += f"&page_token={page_token}"
                    
                response = requests.get(req_url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    bars = data.get('bars', {}).get(ticker, [])
                    if bars:
                        all_bars.extend(bars)
                        
                    page_token = data.get('next_page_token')
                    if not page_token:
                        break
                else:
                    break
                    
            if not all_bars:
                return pd.DataFrame()
            
            df = pd.DataFrame(all_bars)
            df.rename(columns={'t': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'}, inplace=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            # Convert timezone to naive UTC for easy alignment
            df['timestamp'] = df['timestamp'].dt.tz_convert(None) 
            
            return df
        except Exception:
            return pd.DataFrame()

    def calculate_technical_indicators(self, df):
        if df.empty or len(df) < 50: return df
        
        df = df.copy()
        df['SMA20'] = df['close'].rolling(window=20).mean()
        df['STD20'] = df['close'].rolling(window=20).std()
        df['BB_Upper'] = df['SMA20'] + (df['STD20'] * 2)
        df['BB_Lower'] = df['SMA20'] - (df['STD20'] * 2)
        df['BBW'] = (df['BB_Upper'] - df['BB_Lower']) / df['SMA20']
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        df['HV'] = df['close'].pct_change().rolling(window=20).std() * (252**0.5) * 100
        min_hv = df['HV'].rolling(window=252).min()
        max_hv = df['HV'].rolling(window=252).max()
        df['IV_RANK'] = ((df['HV'] - min_hv) / (max_hv - min_hv)) * 100
        df['IV_RANK'] = df['IV_RANK'].fillna(50.0)
        
        df['ATR_14'] = df['high'] - df['low']
        df['ATR_14'] = df['ATR_14'].rolling(window=14).mean()
        df['ATR_EMA10'] = df['ATR_14'].ewm(span=10, adjust=False).mean()
        df['VOL_MOMENTUM'] = df['ATR_14'] > df['ATR_EMA10']
        
        return df

    def run_portfolio_backtest(self, start_date, end_date):
        target_tickers = self.universe.get_universe()
        logger.info(f"Downloading data for {len(target_tickers)} tickers...")
        
        universe_data = {}
        all_timestamps = set()
        
        for ticker in target_tickers:
            df = self.fetch_historical_stock_bars(ticker, start_date, end_date)
            if not df.empty:
                df = self.calculate_technical_indicators(df)
                df.set_index('timestamp', inplace=True)
                universe_data[ticker] = df
                all_timestamps.update(df.index.tolist())
        
        if not all_timestamps:
            logger.error("No chronological data recovered.")
            return
            
        all_timestamps = sorted(list(all_timestamps))
        
        active_positions = {}
        completed_trades = []
        
        logger.info(f"Simulating Portoflio Mechanics chronologically across {len(all_timestamps)} 15-minute vectors...")
        
        for current_time in all_timestamps:
            # 1. Process Exits
            for ticker in list(active_positions.keys()):
                pos = active_positions[ticker]
                df = universe_data[ticker]
                if current_time not in df.index: continue
                
                row = df.loc[current_time]
                current_price = float(row['close'])
                entry_time = pos['entry_time']
                entry_price = pos['entry_price']
                current_action = pos['action']
                
                time_held_hours = (current_time - entry_time).total_seconds() / 3600.0
                
                exit_triggered = False
                exit_reason = ""
                simulated_bucket = pos.get('bucket', 'B')
                
                # Phase 25B: Dynamic Greek Approximation
                if simulated_bucket == "A": gamma_mult, theta_bleed = 12.0, 0.8
                elif simulated_bucket == "B": gamma_mult, theta_bleed = 8.0, 0.5
                else: gamma_mult, theta_bleed = 4.0, 0.2

                if current_action == 1: # Long Straddle
                    move_pct = abs((current_price - entry_price) / entry_price) * 100
                    approx_option_return_pct = (move_pct * gamma_mult) - (time_held_hours * theta_bleed) 
                    approx_option_return_pct = max(-100.0, approx_option_return_pct)
                elif current_action == 2: # Short Iron Condor
                    move_pct = abs((current_price - entry_price) / entry_price) * 100
                    approx_option_return_pct = (time_held_hours * (theta_bleed * 3.0)) - (move_pct * (gamma_mult * 0.6)) 
                    approx_option_return_pct = max(-300.0, min(100.0, approx_option_return_pct))
                elif current_action in [3, 4]: # Credit Spreads
                    move_pct = ((current_price - entry_price) / entry_price) * 100
                    if current_action == 3: move_pct = -move_pct 
                    approx_option_return_pct = (time_held_hours * (theta_bleed * 2.0)) + (move_pct * (gamma_mult * 0.5)) 
                    approx_option_return_pct = max(-200.0, min(100.0, approx_option_return_pct))
                else:
                    approx_option_return_pct = 0.0
                    
                # Phase 24: Hard -20% Stop Loss Guardrail 
                if approx_option_return_pct <= -20.0:
                    exit_triggered = True
                    exit_reason = "Phase 24: Hard -20% Vega Stop"
                    
                # Close Trades at End of Day 
                elif current_time.hour >= 19 and current_time.minute >= 45:
                    exit_reason = "EOD THETA STOP"
                    exit_triggered = True
                    
                if not exit_triggered:
                    if current_action == 1: pos_type = 'straddle'
                    elif current_action == 2: pos_type = 'iron_condor'
                    else: pos_type = 'credit_spread'
                    
                    self.actor_brain.active_simulated_positions = {ticker: {'type': pos_type, 'contract': ticker}}
                    exit_triggered, exit_reason = self.actor_brain.evaluate_exit_trail(ticker, time_held_hours, approx_option_return_pct)

                if exit_triggered:
                    completed_trades.append({
                        'ticker': ticker,
                        'entry_time': entry_time,
                        'exit_time': current_time,
                        'reason': exit_reason,
                        'return_pct': approx_option_return_pct
                    })
                    del active_positions[ticker]

            # Don't enter new trades at end of day
            if current_time.hour >= 19 and current_time.minute >= 30:
                continue

            # 2. Process Entries via Matrix Ranking
            open_slots = max(0, 5 - len(active_positions))
            if open_slots <= 0:
                continue # Hard stop, capacity reached.
                
            candidate_signals = []
            for ticker, df in universe_data.items():
                if ticker in active_positions: continue
                if current_time not in df.index: continue
                
                row = df.loc[current_time]
                rsi = row['RSI'] if pd.notna(row['RSI']) else 50.0
                bbw = row['BBW'] if pd.notna(row['BBW']) else 0.10
                iv_rank = row['IV_RANK']
                vix_mom = row['VOL_MOMENTUM']
                # Phase 24: Hard IV Peak Rejection Filter (> 80th Percentile)
                if iv_rank > 80.0:
                    continue

                action = self.actor_brain.evaluate_entry_vrp(ticker, iv_rank, bbw, rsi, vix_mom)
                if action != 0:
                    # Phase 25B: Store DTE Bucket Data inside the simulator
                    if iv_rank > 60.0: simulated_bucket = "C"
                    elif bbw < 0.03: simulated_bucket = "A"
                    else: simulated_bucket = "B"
                    
                    edge_score = 0
                    if action == 1: edge_score = (100.0 - iv_rank) + (1.0 / max(bbw, 0.01))
                    elif action == 2: edge_score = iv_rank + bbw
                    else: edge_score = abs(50.0 - rsi)
                    
                    candidate_signals.append({
                        'ticker': ticker,
                        'action': action,
                        'edge_score': edge_score,
                        'price': float(row['close']),
                        'bucket': simulated_bucket
                    })

            if candidate_signals:
                candidate_signals.sort(key=lambda x: x['edge_score'], reverse=True)
                for sig in candidate_signals:
                    if open_slots <= 0:
                        break # PORTFOLIO CONSTRAINT ACTIVATED
                    
                    active_positions[sig['ticker']] = {
                        'entry_time': current_time,
                        'entry_price': sig['price'],
                        'action': sig['action'],
                        'bucket': sig['bucket']
                    }
                    open_slots -= 1

        self._print_statistics(completed_trades)

    def _print_statistics(self, trades):
        logger.info("\n==================================================")
        logger.info("    CROSS-SECTIONAL PORTFOLIO PERFORMANCE    ")
        logger.info("==================================================")
        
        if not trades:
            logger.info("No trades executed.")
            return
            
        total_trades = len(trades)
        winning_trades = [t for t in trades if t['return_pct'] > 0]
        losing_trades = [t for t in trades if t['return_pct'] <= 0]
        
        win_rate = (len(winning_trades) / total_trades) * 100
        avg_win = sum([t['return_pct'] for t in winning_trades]) / len(winning_trades) if winning_trades else 0
        avg_loss = sum([t['return_pct'] for t in losing_trades]) / len(losing_trades) if losing_trades else 0
        
        # Cumulative Account PnL assumes exactly 10% risk per trade.
        # So a 50% option return adds 5% relative account value.
        net_relative_pnl = sum([t['return_pct'] * 0.10 for t in trades])
        
        logger.info(f"Total Portfolio Trades:    {total_trades}")
        logger.info(f"Win Rate:                  {win_rate:.2f}%")
        logger.info(f"Average Option Win:        +{avg_win:.2f}%")
        logger.info(f"Average Option Loss:       {avg_loss:.2f}%")
        logger.info(f"Cumulative Portfolio PnL:  {net_relative_pnl:+.2f}%")
        
        reasons = {}
        for t in trades: reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
        
        logger.info("\n--- EXIT BREAKDOWN ---")
        for reason, count in reasons.items(): logger.info(f"{reason}: {count}")
        
        logger.info("==================================================\n")

if __name__ == "__main__":
    backtester = OmegaPortfolioBacktester()
    backtester.run_portfolio_backtest("2025-01-01", "2025-12-31")
