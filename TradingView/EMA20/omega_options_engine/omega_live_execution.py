import logging
import json
import os
import sys
import time
import math
import requests
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf
import numpy as np
import re

import numpy as np

from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest, OptionLatestQuoteRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOptionContractsRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from omega_data_pipeline import OmegaDataPipeline
from omega_actor import OmegaBaselineActor
from omega_universe import OmegaUniverse
from world_state_logger import OptionsWorldStateLogger

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaLiveExecutionEngine:
    """
    The orchestrator for Live execution of the Omega Options Engine.
    Runs via cron job (e.g., every 15 minutes) to fetch live market data,
    evaluate the VRP options math, and submit live Paper Trades.
    """
    
    def __init__(self):
        logger.info("Initializing Omega Live Engine...")
        self.load_credentials()
        
        self.universe = OmegaUniverse()
        self.pipeline = OmegaDataPipeline()
        self.actor_brain = OmegaBaselineActor()
        
        # AI Deprecated (Phase 17). The engine will execute the mathematics baseline 
        # specifically to accrue a massive "World State" macro-economic DB 
        # before Phase 18 meta-learning (LLM / XGBoost) is applied.
        self.ai_brain = None
            
        # Telemetry logger for the AI RL agent to learn from live executions
        self.telemetry = OptionsWorldStateLogger()
        self.discord_webhook = "https://discord.com/api/webhooks/1483706787730559017/tp4nknrFU0Z-_PhIa1sF5ShjjK4Abz9fXZxh7w8m_lMEYyw_TcCU-8u9BYOLNEv5j_7t"

    def post_to_discord(self, message, file_path=None):
        """Sends a notification payload to the Discord channel, supporting embedded CSV attachments."""
        if not self.discord_webhook:
            return
            
        try:
            if file_path and os.path.exists(file_path):
                # Multipart form-data for embedding Excel sheets directly into the chat
                with open(file_path, 'rb') as f:
                    response = requests.post(self.discord_webhook, data={"content": message}, files={'file': f})
            else:
                payload = {"content": message}
                response = requests.post(self.discord_webhook, json=payload)
                
            if response.status_code not in [200, 204]:
                logger.warning(f"Failed to post to Discord: {response.text}")
        except Exception as e:
            logger.warning(f"Discord exception: {e}")
        
    def load_credentials(self):
        """Loads Alpaca API credentials securely from JSON."""
        config_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
                
            # Find the Paper Account in the accounts list
            paper_creds = None
            for account in config.get("accounts", []):
                if account.get("name") == "Paper Account":
                    paper_creds = account
                    break
                    
            if not paper_creds:
                 raise ValueError("Paper Account not found in alpaca_accounts.json")
                 
            self.api_key = paper_creds.get('key')
            self.api_secret = paper_creds.get('secret')
            self.base_url = paper_creds.get('base_url', "https://paper-api.alpaca.markets")
            account_name = paper_creds.get('name', 'Paper Account')
            
            if not self.api_key or not self.api_secret:
                raise ValueError("Missing API credentials.")
                
            self.data_client = StockHistoricalDataClient(self.api_key, self.api_secret)
            self.trading_client = TradingClient(self.api_key, self.api_secret, paper=True)
            self.option_data_client = OptionHistoricalDataClient(self.api_key, self.api_secret)
            
            logger.info(f"Live Execution Engine connected to Alpaca: {account_name}")
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            raise

    def get_atm_option(self, ticker, target_price, option_type="call", min_days=7, max_days=35):
        """Fetches the closest ATM Option Contract symbol (OCC String) from Alpaca."""
        import datetime as dt
        min_dt = dt.date.today() + dt.timedelta(days=min_days)
        max_dt = dt.date.today() + dt.timedelta(days=max_days)
        
        req = GetOptionContractsRequest(
            underlying_symbols=[ticker],
            status="active",
            expiration_date_gte=min_dt.strftime("%Y-%m-%d"),
            expiration_date_lte=max_dt.strftime("%Y-%m-%d"),
            type=option_type
        )
        try:
            res = self.trading_client.get_option_contracts(req)
            contracts = res.option_contracts if hasattr(res, 'option_contracts') else res
            
            if not contracts:
                return None
            
            # Find contract with strike price closest to current underlying target_price
            best_contract = min(contracts, key=lambda c: abs(float(c.strike_price) - target_price))
            return best_contract.symbol
        except Exception as e:
            logger.error(f"Options Chain API Error for {ticker}: {e}")
            return None

    def run_live_loop(self):
        """
        The main loop executed by the cron scheduler.
        1. Checks active options portfolio to process stops (Theta/Profit).
        2. Evaluates the top 10 liquid tickers for new VRP setups.
        """
        logger.info("--- WAKING UP: INITIATING LIVE OPTIONS PIPELINE ---")
        
        # --- 0. Discord Heartbeat ---
        heartbeat_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Silenced Discord Heartbeat for Phase 14 1-minute resolution (prevent discord spam)
        logger.info(f"💓 OMEGA HEARTBEAT | Woke Up at {heartbeat_time} | Scanning {len(self.universe.get_universe())} tickers...")
        
        # --- PHASE 25 COOLDOWN LEDGER READ ---
        # Loads a local memory DB to prevent 60-second recursive revenge-trading flips
        cooldown_file = os.path.join(os.path.dirname(__file__), 'omega_cooldown.json')
        try:
            with open(cooldown_file, 'r') as f:
                cooldown_db = json.load(f)
        except Exception:
            cooldown_db = {}
        import pytz
        current_ts = datetime.now(pytz.timezone('America/New_York')).timestamp()

        # --- 1. Manage Existing Positions ---
        active_symbols = []
        try:
            positions = self.trading_client.get_all_positions()
            if positions:
                for p in positions:
                    match = re.match(r"^([a-zA-Z]+)", p.symbol)
                    if match:
                        active_symbols.append(match.group(1))
                    else:
                        active_symbols.append(p.symbol)
                logger.info(f"Currently monitoring {len(positions)} active network nodes (positions)...")
            
            # --- Unified Multi-Leg Exit Architecture ---
            # Group all open Option Contracts by their root underlying Ticker (e.g. AMZN)
            # If ANY leg of a Straddle hits the +25% Take Profit or the -50% Stop Loss,
            # we must liquidate the ENTIRE Straddle (both Call and Put) simultaneously 
            # to preserve the net mathematical payout and avoid naked directional risk.
            
            # Dictionary keyed by root_ticker to deduplicate identical leg liquidations
            exit_targets = {}
            
            for position in positions:
                symbol = position.symbol # The OCC String
                unrealized_plpc = float(position.unrealized_plpc) * 100 
                
                # Extract Root Ticker
                match = re.match(r"^([a-zA-Z]+)", symbol)
                root_ticker = match.group(1) if match else symbol
                
                if unrealized_plpc > 25.0:
                    exit_targets[root_ticker] = ("PROFIT TARGET", unrealized_plpc)
                elif unrealized_plpc <= -20.0:
                    exit_targets[root_ticker] = ("PHASE 24: HARD -20% VEGA STOP", unrealized_plpc)
                else:
                    # Intraday 2.5-Hour Theta Time Hook
                    time_held, action_type = self._get_position_time_held_and_action(symbol)
                    
                    # CRITICAL FIX: Only apply aggressive Theta Time Stops to Long Options (Action 1: Straddle)
                    # Short Premium (Iron Condors/Spreads) mathematically rely on holding to collect time decay.
                    if action_type == 1 and time_held >= 2.5 and unrealized_plpc < 5.0:
                        exit_targets[root_ticker] = ("THETA TIME STOP (2.5hr)", unrealized_plpc)
                    
            # Execute synchronized paired liquidations
            for root_ticker, data in exit_targets.items():
                reason, trigger_plpc = data
                msg = f"{'🟢' if 'PROFIT' in reason else '🔴'} **{reason} HIT [{root_ticker}]**: Leg at {trigger_plpc:+.1f}%. Liquidating entire Option Structure."
                self.post_to_discord(msg)
                logger.info(msg)
                
                # Close all legs sharing this root ticker
                for position in positions:
                    match = re.match(r"^([a-zA-Z]+)", position.symbol)
                    p_root = match.group(1) if match else position.symbol
                    if p_root == root_ticker:
                        try:
                            # Use native Alpaca positional liquidator to prevent "Naked Selling" margin logic errors
                            o = self.trading_client.close_position(symbol_or_asset_id=position.symbol)
                            self._log_internal_reason(str(o.id), reason, p_root)
                            logger.info(f"Liquidated Leg Component: {position.symbol}")
                            
                            # --- REGISTER 15-MINUTE COOLDOWN BAN ---
                            cooldown_db[p_root] = current_ts
                            try:
                                with open(cooldown_file, 'w') as f: json.dump(cooldown_db, f)
                            except: pass
                        except Exception as oe:
                            logger.error(f"Alpaca Exit Execution Failed for leg {position.symbol}: {oe}")
            # Rebuild Active Symbols completely fresh after liquidations
            active_symbols_list = []
            final_positions = self.trading_client.get_all_positions()
            if final_positions:
                for p in final_positions:
                    match = re.match(r"^([a-zA-Z]+)", p.symbol)
                    if match:
                        active_symbols_list.append(match.group(1))
                    else:
                        active_symbols_list.append(p.symbol)
                        
            # Deduplicate the array so 1 Straddle (Call + Put) only consumes 1 Portfolio Slot
            active_symbols = list(set(active_symbols_list))
        except Exception as e:
            logger.warning(f"Failed to process positional exits: {e}")

        # --- 2. Scan Universe for New Trades ---
        target_tickers = self.universe.get_universe()
        # logger.info(f"Loaded {len(target_tickers)} ultra-liquid options tickers.")
        
        # Max capacity is 5 entirely UNIQUE Tickers (e.g. 5 Straddles = 10 open contracts total)
        open_slots = max(0, 5 - len(active_symbols))
        
        # --- STRUCTURAL SYNCHRONIZATION LOCK ---
        import pytz
        now_est = datetime.now(pytz.timezone('America/New_York'))
        current_minute = now_est.minute
        
        # 1. Prevent generating triggers on yesterday's close during the first 15 minutes of the market
        if now_est.hour == 9 and current_minute >= 30 and current_minute < 45:
            logger.info("Market recently opened. Exits actively monitored. Awaiting first structural 15-Minute candle (09:45 AM) before authorizing new option capital.")
            return

        # 2. Prevent the Engine from entering trades mid-candle (massive slippage against the backtester)
        if current_minute % 15 not in [0, 1, 2, 3]:
            return # Silent heartbeat return since Exits are already mathematically processed.
            
        logger.info(f"Structural 15-Minute Boundary achieved. Scanning target universe for Volatility Risk Premium (VRP) edges...")
        
        # We need roughly 45 trailing days of 15-Minute datastreams to guarantee
        # at least 252 complete 15-minute bars to satisfy the IV_RANK rolling calculation exactly like the Backtester.
        end_dt = datetime.now()
        start_dt = end_dt - pd.Timedelta(days=45) 
        
        # --- 2A. Gather VIX Data and Macro World State Data ---
        macro_context = {
            'Federal_Funds_Rate': 0.0,
            'Yield_Curve_Spread': 0.0,
            'Fed_Total_Assets': 0.0,
            'SPY_Trend': "UNKNOWN"
        }
        
        # 1. Fetch Primary Volatility State (CRITICAL)
        vix_df = pd.DataFrame()
        try:
            req_vix = StockBarsRequest(
                symbol_or_symbols="SPY", 
                timeframe=TimeFrame(15, TimeFrameUnit.Minute), 
                start=start_dt, 
                end=end_dt
            )
            vix_bars = self.data_client.get_stock_bars(req_vix)
            vix_df = vix_bars.df.loc["SPY"]
            vix_df = vix_df.reset_index(level=0, drop=True)
            vix_df.rename(columns={'t': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'}, inplace=True)
            if 'close' in vix_df.columns:
                vix_df['Close'] = vix_df['close']
                vix_df['High'] = vix_df['high']
                vix_df['Low'] = vix_df['low']
                spy_sma200 = vix_df['Close'].rolling(window=200).mean().iloc[-1]
                current_spy = vix_df['Close'].iloc[-1]
                macro_context['SPY_Trend'] = "BULLISH" if current_spy > spy_sma200 else "BEARISH"
        except Exception as e:
            logger.warning(f"Failed to fetch SPY Volatility Proxy from Alpaca: {e}")
            try:
                # Emergency Fallback to YFinance
                vix_df = yf.download("SPY", start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"), progress=False)
                if isinstance(vix_df.columns, pd.MultiIndex):
                    vix_df.columns = vix_df.columns.get_level_values(0)
            except Exception as e2:
                logger.error(f"FATAL: YFinance Fallback failed. {e2}")

        # 2. Fetch Auxiliary FRED Macro Telemetry (NON-CRITICAL)
        try:
            fred_api_key = "6edacb8953cb602d0e88ba8693891f9d"
            res = requests.get(f"https://api.stlouisfed.org/fred/series/observations?series_id=DFF&api_key={fred_api_key}&file_type=json", timeout=5)
            if res.status_code == 200:
                obs = res.json().get('observations', [])
                if obs: macro_context['Federal_Funds_Rate'] = float(obs[-1]['value'])
        except Exception as e:
            logger.warning(f"FRED API Ping Timeout: {e}")

        def calculate_live_indicators(df):
            if df.empty or len(df) < 50:
                return df
                
            # Simulate IV proxy using ATR for the sake of the mathematical pipeline
            tr1 = df['High'] - df['Low']
            tr2 = (df['High'] - df['Close'].shift(1)).abs()
            tr3 = (df['Low'] - df['Close'].shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['ATR'] = tr.rolling(window=14).mean() 
            df['IV_PROXY'] = df['ATR'] / df['Close'] * 100 
            
            # IV Rank (Rolling 252 days, or max available)
            roll_window = min(252, len(df))
            rolling_min = df['IV_PROXY'].rolling(window=roll_window, min_periods=20).min()
            rolling_max = df['IV_PROXY'].rolling(window=roll_window, min_periods=20).max()
            df['IV_RANK'] = ((df['IV_PROXY'] - rolling_min) / (rolling_max - rolling_min)) * 100
            df['IV_RANK'] = df['IV_RANK'].fillna(0)
            
            # Bollinger Bands
            rolling_mean = df['Close'].rolling(window=20).mean()
            rolling_std = df['Close'].rolling(window=20).std()
            bbl = rolling_mean - (rolling_std * 2)
            bbu = rolling_mean + (rolling_std * 2)
            df['BBW'] = ((bbu - bbl) / rolling_mean)
            df['BBW'] = df['BBW'].fillna(0.10)
                
            # RSI
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            df['RSI'] = df['RSI'].fillna(50)
            
            # Simulated Volatility Momentum (Moving average of IV proxy)
            df['VOL_MOMENTUM'] = df['IV_PROXY'].diff(3) 
            df['VOL_MOMENTUM'] = df['VOL_MOMENTUM'].fillna(0)
            
            return df
        
        # Pull live macro state (VIX)
        try:
            vix_state = calculate_live_indicators(vix_df)
            latest_vix = vix_state.iloc[-1]
            # VRP relies on positive VIX momentum to trust downside protection
            vix_momentum = float(latest_vix['VOL_MOMENTUM'])
        except Exception as e:
            logger.error(f"FATAL: Could not resolve VIX Macro layer. Halting execution. {e}")
            return

        candidate_signals = []
        held_edges = {}
        for ticker in target_tickers:
            is_active_holding = (ticker in active_symbols)
                
            # --- PHASE 25: REVENGE-TRADING COOLDOWN QUARANTINE ---
            if ticker in cooldown_db and not is_active_holding:
                if (current_ts - cooldown_db[ticker]) < (15 * 60):
                    logger.info(f"[{ticker}] is under a strict 15-Minute Cooldown ban from a recent Liquidation event. Bypassing Entry scanning completely.")
                    continue
            
            try:
                # Poll Real-time 15-minute OHLC data natively from Alpaca
                req = StockBarsRequest(
                    symbol_or_symbols=[ticker],
                    timeframe=TimeFrame(15, TimeFrameUnit.Minute),
                    start=start_dt,
                    end=end_dt
                )
                bars = self.data_client.get_stock_bars(req)
                if bars.df.empty:
                    continue
                    
                # Alpaca returns a MultiIndex (symbol, timestamp). Extract the ticker's dataframe.
                df = bars.df.loc[ticker].copy()
                # Rename the columns to match what the 'ta' lib expects (Capitalized)
                df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
                
                # Verify the structural integrity of the trailing timeframe before indexing
                if not df.empty:
                    latest_bar_ts = pd.to_datetime(df.index[-1]).tz_convert('America/New_York') if df.index[-1].tzinfo is None else df.index[-1]
                    # If the latest bar's timestamp + 15 minutes is still in the future, the physical candle hasn't closed.
                    if now_est < (latest_bar_ts + timedelta(minutes=15)):
                        df = df.iloc[:-1] # Brutally amputate the incomplete vibrating bar to preserve mathematical validity.
                
                if len(df) < 50:
                    continue
                    
                df = calculate_live_indicators(df)
                latest_bar = df.iloc[-1]
                
                # Extract VRP Mathematics at the Live Edge
                iv_rank = float(latest_bar['IV_RANK'])
                bbw = float(latest_bar['BBW']) if not pd.isna(latest_bar['BBW']) else 0.10
                rsi = float(latest_bar['RSI'])
                
                # To prevent entry slippage, we grab the physical real-time quote instead of the delayed candle close
                current_price = float(latest_bar['Close'])
                
                # --- Dynamic Rotation Score Profiling for Existing Holds ---
                if is_active_holding:
                    # Query exactly what mathematical structure we hold on this ticker
                    _, action_type = self._get_position_time_held_and_action(ticker)
                    if action_type > 0:
                        if action_type == 1: edge_score = (100.0 - iv_rank) + (1.0 / max(bbw, 0.01))
                        elif action_type == 2: edge_score = iv_rank + bbw
                        else: edge_score = abs(50.0 - rsi)
                        held_edges[ticker] = edge_score
                    continue # Finished scoring our held position, prevent entering it again!
                
                # Phase 24: Hard IV Rank rejection filter
                if iv_rank > 80.0:
                    logger.warning(f"Bypass [{ticker}]: IV Rank ({iv_rank:.1f}) is in the Danger Zone (>80th percentile). Rejecting entry to preserve Vega decay.")
                    continue
                
                math_action = self.actor_brain.evaluate_entry_vrp(ticker, iv_rank, bbw, rsi, vix_momentum)
                
                if math_action != 0:
                    strat_map = {0: "Do Nothing", 1: "Long Straddle", 2: "Short Iron Condor", 3: "Call Credit Spread", 4: "Put Credit Spread"}
                    math_strat = strat_map.get(math_action, "Do Nothing")
                    
                    # --- Algorithmic Edge Scoring (Phase 21) ---
                    # Defines priority for execution so the engine buys the absolute best structures first.
                    if math_action == 1: # Volatility Squeeze (Loves Low IV, Low BBW)
                        edge_score = (100.0 - iv_rank) + (1.0 / max(bbw, 0.01))
                    elif math_action == 2: # Fear Harvest (Loves High IV, Wide BBW)
                        edge_score = iv_rank + bbw
                    else: # Statistical Reversion (Loves RSI Extremes)
                        edge_score = abs(50.0 - rsi)
                        
                    candidate_signals.append({
                        'ticker': ticker,
                        'action': math_action,
                        'strategy': math_strat,
                        'edge_score': edge_score,
                        'iv_rank': iv_rank,
                        'bbw': bbw,
                        'rsi': rsi,
                        'current_price': current_price
                    })

            except Exception as e:
                logger.error(f"Mathematical extraction failed on {ticker}: {e}")

        # --- Algorithmic Ranking Execution (Phase 21) ---
        if candidate_signals:
            # Sort highest edge score to lowest
            candidate_signals.sort(key=lambda x: x['edge_score'], reverse=True)
            logger.info(f"Algorithmic Matrix Ranked {len(candidate_signals)} pristine targets.")
            
            account = self.trading_client.get_account()
            total_equity = float(account.equity)
            buying_power = float(account.buying_power)
            
            # Global Position Limit Rule (Maximum 5 concurrent holdings)
            open_slots = max(0, 5 - len(active_symbols))
            max_alloc_percent = 0.10
            cash_allocation_per_trade = total_equity * max_alloc_percent
            
            # --- DYNAMIC PORTFOLIO ROTATION CORE ---
            # If the matrix is violently skewed, forcibly override the open_slots restriction by executing a 
            # capital exchange where the weakest active portfolio member is liquidated manually.
            if open_slots == 0 and len(active_symbols) >= 5 and held_edges and candidate_signals:
                best_new_signal = candidate_signals[0]
                weakest_ticker = min(held_edges, key=held_edges.get)
                weakest_score = held_edges[weakest_ticker]
                
                logger.info(f"🔄 DYNAMIC ROTATION CHECK: Evaluated incoming Alpha `{best_new_signal['ticker']}` (Edge: {best_new_signal['edge_score']:.1f}) against weakest holding `{weakest_ticker}` (Edge: {weakest_score:.1f}).")
                
                # 3.0x Multiplier Buffer to prevent high-frequency spread slippage
                if best_new_signal['edge_score'] >= (weakest_score * 3.0):
                    msg = f"🔄 **DYNAMIC ROTATION**: Alpha 3.0x Multiplier unlocked. Liquidating weak `{weakest_ticker}` ({weakest_score:.1f}) to deploy capital to dominant `{best_new_signal['ticker']}` ({best_new_signal['edge_score']:.1f})."
                    self.post_to_discord(msg)
                    logger.warning(msg)
                    
                    # Liquidate the underlying options completely across all components using synced Alpaca Market Orders
                    positions_to_close = self.trading_client.get_all_positions()
                    for pos in positions_to_close:
                        match = re.match(r"^([a-zA-Z]+)", pos.symbol)
                        p_root = match.group(1) if match else pos.symbol
                        if p_root == weakest_ticker:
                            try:
                                co = self.trading_client.close_position(symbol_or_asset_id=pos.symbol)
                                self._log_internal_reason(str(co.id), "DYNAMIC ROTATION LIQUIDATION", weakest_ticker)
                                logger.info(f"Liquidated Leg Component: {pos.symbol} for Dynamic Rotation")
                                
                                # --- REGISTER 15-MINUTE COOLDOWN BAN ---
                                cooldown_db[weakest_ticker] = current_ts
                                try:
                                    with open(cooldown_file, 'w') as f: json.dump(cooldown_db, f)
                                except: pass
                            except Exception as oe:
                                logger.error(f"Rotation Execution Failed for leg {pos.symbol}: {oe}")
                                
                    open_slots += 1 # We successfully cleared exactly 1 unit of capital allocation
            
            for signal in candidate_signals:
                if open_slots <= 0:
                    logger.info("Maximum Portfolio Capacity (5) Reached. Ranking Matrix cutoff engaged.")
                    break
                    
                if buying_power < cash_allocation_per_trade:
                    logger.warning(f"Insufficient Buying Power. Required: ${cash_allocation_per_trade:,.2f}. Halting.")
                    break
                    
                ticker = signal['ticker']
                math_action = signal['action']
                math_strat = signal['strategy']
                iv_rank = signal['iv_rank']
                bbw = signal['bbw']
                rsi = signal['rsi']
                current_price = signal['current_price']
                
                # Phase 25 Revision: Mathematical Duration (DTE) Targeting
                import datetime as dt
                
                # Rule 1: High Volatility Environment (Defense against Vega/Theta Crush)
                # If IV is structurally elevated, we are mathematically forced to buy Time (Quarterlies) 
                # because long-dated options have exponentially lower Vega sensitivity.
                if iv_rank > 60.0:
                    min_days, max_days = 45, 90
                    bucket = "C (High IV Defense)"
                    
                # Rule 2: Violent Imminent Squeeze (Explosive Gamma Leverage)
                # If Bollinger Width is impossibly tight, the kinetic energy is about to release immediately.
                # We do not need to overpay for Time; we buy near-term options to maximize Delta/Gamma torque.
                elif bbw < 0.03:
                    min_days, max_days = 7, 21
                    bucket = "A (Gamma Kinetic Squeeze)"
                    
                # Rule 3: Standard Reversion (Theta Decay Anchor)
                # Standard mechanical reversion baseline.
                else:
                    min_days, max_days = 21, 45
                    bucket = "B (Monthly Mean Reversion)"
                
                # Native Options Execution Protocol (Phase 21 + Phase 22)
                try:
                    atm_call_occ = self.get_atm_option(ticker, current_price, "call", min_days, max_days)
                    atm_put_occ = self.get_atm_option(ticker, current_price, "put", min_days, max_days)
                    
                    if not atm_call_occ or not atm_put_occ:
                        logger.warning(f"Failed to locate Native OCC Option Chain for {ticker}. Skipping execution.")
                        continue
                        
                    # Calculate true DTE from the OCC string
                    actual_dte = 14
                    match = re.search(r"\d{6}", atm_call_occ)
                    if match:
                        exp_str = match.group(0)
                        exp_date = dt.datetime.strptime(exp_str, "%y%m%d").date()
                        actual_dte = max(0, (exp_date - dt.date.today()).days)
                        
                    math_strat_logged = f"Math:{math_strat} ({actual_dte} DTE)"
                    
                    # Deep State Logging for AI Reinforcement Learning (World State Update)
                    self.telemetry.log_state_and_action(
                        timestamp=end_dt.isoformat(),
                        ticker=ticker,
                        underlying_price=current_price,
                        vix=vix_momentum,
                        spy_trend=rsi,
                        options_chain=pd.DataFrame(),
                        action_type=math_action,
                        macro_context=macro_context,
                        selected_contract=math_strat_logged,
                        iv_rank=iv_rank,
                        bbw=bbw,
                        target_dte=actual_dte
                    )
                    logger.info(f"Logged Live Deep State Vector for {ticker} [Bucket {bucket}: {actual_dte} DTE].")
                        
                    # Phase 22: Live Premium Extraction for Dynamic Position Sizing
                    def get_ask_price(occ_sym):
                        try:
                            req_quote = OptionLatestQuoteRequest(symbol_or_symbols=[occ_sym])
                            res_quote = self.option_data_client.get_option_latest_quote(req_quote)
                            quote = res_quote.get(occ_sym)
                            if quote and hasattr(quote, 'ask_price'):
                                return float(quote.ask_price)
                            elif quote and isinstance(quote, dict) and 'ask_price' in quote:
                                return float(quote['ask_price'])
                            return 2.50 # Extreme fallback logic: $250 default option price
                        except Exception as qe:
                            logger.error(f"Live Quote Failed on {occ_sym}: {qe}. Scaling off fallback default.")
                            return 2.50
                            
                    call_cost = put_cost = 0.0
                    if math_action in [1, 4]: call_cost = get_ask_price(atm_call_occ) * 100.0
                    if math_action in [1, 3]: put_cost = get_ask_price(atm_put_occ) * 100.0
                    
                    total_premium = 0.0
                    if math_action == 1: total_premium = call_cost + put_cost
                    elif math_action == 3: total_premium = put_cost
                    elif math_action == 4: total_premium = call_cost
                    
                    # Target sizing = Exactly 10% Cash (e.g. $10,000) divided by the Live Option Structure Cost
                    qty_to_buy = 1
                    if total_premium > 0:
                        qty_to_buy = max(1, int(cash_allocation_per_trade / total_premium))
                        
                    # Saftey check against overextending API logic breakdown
                    actual_cost = total_premium * qty_to_buy
                    if actual_cost > buying_power and qty_to_buy > 1:
                        qty_to_buy = max(1, int(buying_power / total_premium))
                        
                    qty_to_buy = max(1, qty_to_buy) # Minimum structural requirement is exactly 1 contract
                        
                    e_logic = "Unknown"
                    if math_action == 1: e_logic = "Volatility Squeeze"
                    elif math_action == 2: e_logic = "Fear Premium Harvest"
                    elif math_action == 3: e_logic = "Overbought Reversion"
                    elif math_action == 4: e_logic = "Oversold Reversion"
                        
                    run_ctx = {
                        "iv_rank": round(iv_rank, 2),
                        "bbw": round(bbw, 5),
                        "rsi": round(rsi, 2),
                        "vix_momentum": round(vix_momentum, 2)
                    }
                        
                    # Execute natively using dynamically calculated 'qty_to_buy' 
                    if math_action == 1: 
                        req_c = MarketOrderRequest(symbol=atm_call_occ, qty=qty_to_buy, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                        req_p = MarketOrderRequest(symbol=atm_put_occ, qty=qty_to_buy, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                        o_c = self.trading_client.submit_order(order_data=req_c)
                        o_p = self.trading_client.submit_order(order_data=req_p)
                        self._log_internal_reason(str(o_c.id), "LIVE ENTRY", ticker, trade_type=math_strat, entry_logic=e_logic, context=run_ctx)
                        self._log_internal_reason(str(o_p.id), "LIVE ENTRY", ticker, trade_type=math_strat, entry_logic=e_logic, context=run_ctx)
                        buying_power -= (total_premium * qty_to_buy)
                        open_slots -= 1
                        active_symbols.append(ticker)
                        
                        msg = f"🟢 **OMEGA NATIVE OPTIONS RANKED [{ticker}]**: {math_strat} | Size: {qty_to_buy} Lots (~${(total_premium * qty_to_buy):,.2f}) | OCC Target: {atm_call_occ}"
                        logger.info(msg)
                        self.post_to_discord(msg)
                        
                    elif math_action == 2: # Short Iron Condor (High IV Harvest)
                        req_c = MarketOrderRequest(symbol=atm_call_occ, qty=qty_to_buy, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                        req_p = MarketOrderRequest(symbol=atm_put_occ, qty=qty_to_buy, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                        o_c = self.trading_client.submit_order(order_data=req_c)
                        o_p = self.trading_client.submit_order(order_data=req_p)
                        self._log_internal_reason(str(o_c.id), "LIVE ENTRY", ticker, trade_type=math_strat, entry_logic=e_logic, context=run_ctx)
                        self._log_internal_reason(str(o_p.id), "LIVE ENTRY", ticker, trade_type=math_strat, entry_logic=e_logic, context=run_ctx)
                        buying_power -= (total_premium * qty_to_buy)
                        open_slots -= 1
                        active_symbols.append(ticker)
                        
                        msg = f"🟢 **OMEGA NATIVE OPTIONS RANKED [{ticker}]**: {math_strat} (Bucket C Vega Defense Mode) | Size: {qty_to_buy} Lots | OCC Targets: {atm_call_occ} & {atm_put_occ}"
                        logger.info(msg)
                        self.post_to_discord(msg)
                        
                    elif math_action == 3: # Call Credit Spread (Bearish Reversion)
                        req_p = MarketOrderRequest(symbol=atm_put_occ, qty=qty_to_buy, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                        o_p = self.trading_client.submit_order(order_data=req_p)
                        self._log_internal_reason(str(o_p.id), "LIVE ENTRY", ticker, trade_type=math_strat, entry_logic=e_logic, context=run_ctx)
                        buying_power -= (total_premium * qty_to_buy)
                        open_slots -= 1
                        active_symbols.append(ticker)
                        
                        msg = f"🟢 **OMEGA NATIVE OPTIONS RANKED [{ticker}]**: {math_strat} (Directional Put Fallback) | Size: {qty_to_buy} Lots (~${(total_premium * qty_to_buy):,.2f}) | OCC Target: {atm_put_occ}"
                        logger.info(msg)
                        self.post_to_discord(msg)
                        
                    elif math_action == 4: # Put Credit Spread (Bullish Reversion)
                        req_c = MarketOrderRequest(symbol=atm_call_occ, qty=qty_to_buy, side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
                        o_c = self.trading_client.submit_order(order_data=req_c)
                        self._log_internal_reason(str(o_c.id), "LIVE ENTRY", ticker, trade_type=math_strat, entry_logic=e_logic, context=run_ctx)
                        buying_power -= (total_premium * qty_to_buy)
                        open_slots -= 1
                        active_symbols.append(ticker)
                        
                        msg = f"🟢 **OMEGA NATIVE OPTIONS RANKED [{ticker}]**: {math_strat} (Directional Call Fallback) | Size: {qty_to_buy} Lots (~${(total_premium * qty_to_buy):,.2f}) | OCC Target: {atm_call_occ}"
                        logger.info(msg)
                        self.post_to_discord(msg)
                        
                except Exception as oe:
                    logger.error(f"Alpaca Ranked Exec Failed on Options Chain for {ticker}: {oe}")

        logger.info("--- SHUTTING DOWN: LIVE OPTIONS PIPELINE COMPLETE ---")

    def _log_internal_reason(self, order_id: str, reason: str, symbol: str, trade_type: str = "Unknown", entry_logic: str = "Unknown", context: dict = None):
        import json, os
        path = os.path.join(os.path.dirname(__file__), 'omega_order_reasons.json')
        data = {}
        if os.path.exists(path):
            try:
                with open(path, 'r') as f: data = json.load(f)
            except: pass
        data[order_id] = {"reason": reason, "symbol": symbol, "trade_type": trade_type, "entry_logic": entry_logic, "context": context or {}}
        with open(path, 'w') as f: json.dump(data, f)

    def _get_position_time_held_and_action(self, occ_symbol: str) -> tuple[float, int]:
        """ Queries the local SQLite telemetry DB to determine how long an OCC leg has been held, and its Strategy Action Integer. """
        import sqlite3
        import re
        
        try:
            # 1. Extract raw Root Ticker from the OCC Option String (e.g. SPY251219C00500000 -> SPY)
            match = re.match(r"^([a-zA-Z]+)", occ_symbol)
            root_ticker = match.group(1) if match else occ_symbol
            
            db_path = os.path.join(os.path.dirname(__file__), 'omega_telemetry.db')
            if not os.path.exists(db_path): return 0.0, 0
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 2. Query the exact timestamp and action_type the AI took for this ticker
            q = '''
                SELECT s.timestamp, a.action_type FROM underlying_state s
                JOIN engine_actions a ON s.state_id = a.state_id
                WHERE s.ticker = ? AND a.action_type > 0
                ORDER BY s.timestamp DESC LIMIT 1
            '''
            cursor.execute(q, (root_ticker,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                entry_dt = pd.to_datetime(row[0])
                action_type = int(row[1]) if row[1] is not None else 0
                now_dt = datetime.now()
                return (now_dt - entry_dt).total_seconds() / 3600.0, action_type
        except Exception:
            pass
        return 0.0, 0

    def liquidate_all_positions(self, reason: str):
        logger.info(f"--- INITIATING MASS LIQUIDATION: {reason} ---")
        try:
            positions = self.trading_client.get_all_positions()
            if not positions:
                logger.info("Portfolio is currently flat. No liquidations required.")
                return
                
            for position in positions:
                try:
                    qty_abs = abs(float(position.qty))
                    close_side = OrderSide.SELL if float(position.qty) > 0 else OrderSide.BUY
                    req = MarketOrderRequest(symbol=position.symbol, qty=qty_abs, side=close_side, time_in_force=TimeInForce.DAY)
                    o = self.trading_client.submit_order(order_data=req)
                    
                    match = re.match(r"^([a-zA-Z]+)", position.symbol)
                    p_root = match.group(1) if match else position.symbol
                    self._log_internal_reason(str(o.id), reason, p_root)
                    
                    logger.info(f"Liquidated Leg Component: {position.symbol}")
                except Exception as oe:
                    logger.error(f"Alpaca Exit Execution Failed for leg {position.symbol}: {oe}")
                    
            msg = f"🔴 **{reason} COMMAND EXECUTED**: All {len(positions)} active option vectors forcefully liquidated."
            self.post_to_discord(msg)
            
        except Exception as e:
            logger.error(f"Failed to execute mass liquidation: {e}")

    def generate_eod_report(self, target_date_str=None):
        """
        Fired rigorously after 3:45 PM EST option liquidation boundary. Wraps the daily PnL,
        and surgically queries the Alpaca `/v2/activities/FILL` ledger to physically upload
        the authoritative transactional array directly to the user's Discord.
        """
        logger.info("--- GENERATING OMEGA END OF DAY REPORT ---")
        try:
            account = self.trading_client.get_account()
            equity = float(account.equity)
            last_equity = float(account.last_equity)
            daily_pnl = equity - last_equity
            daily_pnl_pct = (daily_pnl / last_equity) * 100 if last_equity > 0 else 0
            
            positions = self.trading_client.get_all_positions()
            
            msg = f"📊 **OMEGA EOD SCOREBOARD**\n"
            msg += f"**Total Portfolio:** ${equity:,.2f} ({'+' if daily_pnl >= 0 else ''}${daily_pnl:,.2f} / {'+' if daily_pnl_pct >= 0 else ''}{daily_pnl_pct:.2f}% Today)\n\n"
            
            if positions:
                msg += f"**Open Overnight Contracts ({len(positions)}):**\n"
                for p in positions:
                    unrealized = float(p.unrealized_plpc) * 100
                    msg += f"- `{p.symbol}`: {p.qty} contracts | {unrealized:+.2f}% PnL\n"
            else:
                msg += "**Open Overnight Contracts:** None. Going to sleep flat.\n"

            # --------------------------------------------------------------------------------
            # PHASE 28: ALPACA ACTIVITIES TRANSACTIONAL REPORTING w/ REASON CACHE
            # --------------------------------------------------------------------------------
            csv_path = None
            today_str = datetime.now().strftime('%Y-%m-%d')
            
            try:
                import requests
                import json
                import pytz
                import pandas as pd
                
                # Load the localized algorithmic reasons cache to map UUID -> Reason
                reason_map = {}
                path = os.path.join(os.path.dirname(__file__), 'omega_order_reasons.json')
                if os.path.exists(path):
                    try:
                        with open(path, 'r') as f:
                            reason_map = json.load(f)
                    except: pass
                
                # Circumvent broken alpaca-py model by hitting the raw REST API
                url = "https://paper-api.alpaca.markets/v2/account/activities/FILL"
                headers = {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret}
                # Fix: Alpaca requires YYYY-MM-DD exactly to return all fills for that day
                dt_str = target_date_str if target_date_str else datetime.now().astimezone(pytz.timezone('America/New_York')).strftime('%Y-%m-%d')
                response = requests.get(url, headers=headers, params={"date": dt_str})
                fills = response.json() if response.status_code == 200 else []
                
                rows = []
                if fills:
                    # Group Fills by Contract
                    contract_fills = {}
                    for f in fills:
                        sym = f['symbol']
                        if sym not in contract_fills: contract_fills[sym] = []
                        contract_fills[sym].append(f)
                        
                    for sym, fill_list in contract_fills.items():
                        # Sort chronologically
                        fill_list.sort(key=lambda x: x['transaction_time'])
                        
                        buy_qty = 0; sell_qty = 0
                        buy_cash = 0.0; sell_cash = 0.0
                        buy_times = []; sell_times = []
                        
                        alg_intent = "Unknown Exit"
                        root_ticker = sym
                        trade_type = "Unknown Strategy"
                        entry_logic = "Unknown Reason"
                        iv_rank_str = "N/A"
                        bbw_str = "N/A"
                        rsi_str = "N/A"
                        vix_str = "N/A"
                        
                        for f in fill_list:
                            order_id = f.get('order_id', '')
                            bot_reason = reason_map.get(order_id, {}).get("reason", "")
                            if bot_reason and "LIVE ENTRY" not in bot_reason: 
                                alg_intent = bot_reason # Use the Exit reason as intent
                                
                            t_type = reason_map.get(order_id, {}).get("trade_type", "")
                            if t_type and t_type != "Unknown": trade_type = t_type
                            
                            e_log = reason_map.get(order_id, {}).get("entry_logic", "")
                            if e_log and e_log != "Unknown": entry_logic = e_log
                            
                            ctx = reason_map.get(order_id, {}).get("context", {})
                            if ctx:
                                if ctx.get("iv_rank") is not None: iv_rank_str = str(ctx.get("iv_rank"))
                                if ctx.get("bbw") is not None: bbw_str = str(ctx.get("bbw"))
                                if ctx.get("rsi") is not None: rsi_str = str(ctx.get("rsi"))
                                if ctx.get("vix_momentum") is not None: vix_str = str(ctx.get("vix_momentum"))
                                
                            r_sym = reason_map.get(order_id, {}).get("symbol", "")
                            if r_sym: root_ticker = r_sym
                                
                            q = float(f['qty'])
                            p = float(f['price'])
                            t = pd.to_datetime(f['transaction_time']).tz_convert('America/New_York').strftime('%H:%M:%S')
                            
                            if str(f['side']).lower() == 'buy':
                                buy_qty += q
                                buy_cash += (p * q * 100)
                                buy_times.append(t)
                            else:
                                sell_qty += q
                                sell_cash += (p * q * 100)
                                sell_times.append(t)
                                
                        avg_buy = (buy_cash / (buy_qty * 100)) if buy_qty > 0 else 0
                        avg_sell = (sell_cash / (sell_qty * 100)) if sell_qty > 0 else 0
                        
                        # Calculate PnL if perfectly flat (Guillotine worked)
                        realized_pnl = 0.0
                        if buy_qty > 0 and sell_qty > 0:
                            matched_qty = min(buy_qty, sell_qty)
                            matched_buy_cash = matched_qty * avg_buy * 100
                            matched_sell_cash = matched_qty * avg_sell * 100
                            # If we bought (e.g. Straddle), PnL is Sell Cash - Buy Cash
                            # If we sold (e.g. Credit Spread), we "sold to open", so we received Sell Cash and paid Buy Cash to close!
                            # The math is universally the same: Cash In - Cash Out
                            realized_pnl = matched_sell_cash - matched_buy_cash
                            
                        # Format the output row
                        row = {
                            "Ticker": root_ticker,
                            "Trade Type": trade_type,
                            "Entry Logic": entry_logic,
                            "IV Rank": iv_rank_str,
                            "BBW": bbw_str,
                            "RSI": rsi_str,
                            "VIX": vix_str,
                            "Contract Strategy": sym,
                            "Exit Reason": alg_intent,
                            "Qty Traded": int(max(buy_qty, sell_qty)),
                            "Buy Time(s)": " | ".join(buy_times) if buy_times else "N/A",
                            "Avg Buy Price": f"${avg_buy:.2f}" if avg_buy > 0 else "N/A",
                            "Sell Time(s)": " | ".join(sell_times) if sell_times else "N/A",
                            "Avg Sell Price": f"${avg_sell:.2f}" if avg_sell > 0 else "N/A",
                            "Realized PnL": f"${realized_pnl:.2f}" if realized_pnl != 0 else "$0.00"
                        }
                        rows.append(row)
                        
                if rows:
                    df = pd.DataFrame(rows)
                    # Reverse chronological
                    df = df.iloc[::-1].reset_index(drop=True)
                    
                    csv_name = f"omega_ledger_{dt_str}.csv"
                    csv_path = os.path.join(os.path.dirname(__file__), csv_name)
                    df.to_csv(csv_path, index=False)
                    
                    # --------------------------------------------------------------------------------
                    # PHASE 29: RELATIONAL HUMAN LEDGER MIGRATION (DEPRECATING CSV RELIANCE)
                    # --------------------------------------------------------------------------------
                    try:
                        import sqlite3
                        db_path = os.path.join(os.path.dirname(__file__), 'omega_telemetry.db')
                        if os.path.exists(db_path):
                            conn = sqlite3.connect(db_path)
                            df_sql = df.copy()
                            df_sql['Execution_Date'] = dt_str
                            # Prevent duplication if the script runs twice a day manually
                            try:
                                conn.execute("DELETE FROM discord_human_ledger WHERE Execution_Date = ?", (dt_str,))
                            except sqlite3.OperationalError: pass # Table doesn't exist yet
                            
                            df_sql.to_sql('discord_human_ledger', conn, if_exists='append', index=False)
                            conn.close()
                            logger.info("Human Ledger successfully committed to Omega SQLite Telemetry Database.")
                    except Exception as sqle:
                        logger.warning(f"Failed to commit Human Ledger to Relational DB: {sqle}")
                    
                    msg += f"\n📁 **Daily Trade Ledger Attached:** `{len(df)} Options Contracts Cleared`"
                    msg += f"\n*Fully aggregated Buy/Sell pairing timestamps and absolute Realized PnL.*"
                else:
                    msg += f"\n📁 **Trade Ledger:** 0 Official Fills executed today."
                    
            except Exception as db_e:
                logger.warning(f"Failed to extract Full Transactional Ledger from REST API: {db_e}")
                
            self.post_to_discord(msg, file_path=csv_path)
            logger.info("EOD Report and Spreadsheet payload dispatched successfully.")
            
        except Exception as e:
            logger.error(f"Failed to generate EOD report: {e}")

if __name__ == "__main__":
    engine = OmegaLiveExecutionEngine()
    engine.run_live_loop()
