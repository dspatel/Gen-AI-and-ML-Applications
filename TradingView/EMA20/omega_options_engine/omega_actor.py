import logging
import random
import time
from datetime import datetime
import pandas as pd
from omega_universe import OmegaUniverse
from omega_data_pipeline import OmegaDataPipeline
from world_state_logger import OptionsWorldStateLogger
from omega_execution import OmegaExecutionEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaBaselineActor:
    """
    The R6-Inspired "Baseline Brain". 
    Uses Opening Range Breakout (ORB) for entries and an EMA20 Trailing Stop for exits.
    This creates a structured, logical dataset for the future RL Agent to beat.
    """
    def __init__(self):
        self.universe = OmegaUniverse()
        self.pipeline = OmegaDataPipeline()
        self.logger_db = OptionsWorldStateLogger()
        self.execution = OmegaExecutionEngine()
        
        # Keep track of active baseline positions so we can trail the stop
        # In a full system, this would query the Alpaca /v2/positions endpoint
        self.active_simulated_positions = {} 
        
    def find_target_contracts(self, df):
        """
        Filters the options chain down to a 30-day ATM Call/Put and Out-of-the-Money options
        required for constructing spreads.
        """
        if df.empty:
            return None, None
            
        target_dte = 30
        df['dte_diff'] = abs(df['dte'] - target_dte)
        best_dte = df['dte_diff'].min()
        target_df = df[df['dte_diff'] == best_dte]
        
        calls = target_df[target_df['option_type'] == 'call'].copy()
        puts = target_df[target_df['option_type'] == 'put'].copy()
        
        target_call = None
        target_put = None

        if not calls.empty:
            calls['delta_diff'] = abs(calls['delta'] - 0.50)
            target_call = calls.loc[calls['delta_diff'].idxmin()]
            
        if not puts.empty:
            puts['delta_diff'] = abs(puts['delta'] - (-0.50))
            target_put = puts.loc[puts['delta_diff'].idxmin()]

        return target_call, target_put

    # Options Action Dictionary
    # 0 = Do Nothing
    # 1 = Buy ATM Straddle (Low IV, Tight BBW)
    # 2 = Sell OTM Iron Condor (High IV)
    # 3 = Sell Call Credit Spread (RSI > 80, Mid IV)
    # 4 = Sell Put Credit Spread (RSI < 20, Mid IV)

    def evaluate_entry_vrp(self, ticker, iv_rank, bbw, rsi, vix_momentum_positive) -> int:
        """
        Evaluates the Volatility Risk Premium (VRP) conditions.
        Ignores directional momentum in favor of Volatility Expansion and Mean Reversion.
        """
        # 1. The Volatility Squeeze (Expansion Imminent)
        if iv_rank < 35.0 and bbw < 0.08: # Loosened for scale (Phase 11)
            if not vix_momentum_positive:
                logger.info(f"VRP Trigger Rejected [{ticker}]: IV is low, but VIX Term Structure is bleeding. No immediate catalyst.")
                return 0
            logger.info(f"VRP Trigger [{ticker}]: Volatility Squeeze detected. Low IV + Tight BBW + Rising VIX.")
            return 1 # Buy Straddle
            
        # 2. The Fear Premium Harvest (Crush Imminent)
        elif iv_rank > 65.0: # Loosened from 80 (Phase 11)
            logger.info(f"VRP Trigger [{ticker}]: Fear Premium detected. Elevated IV.")
            return 2 # Sell Iron Condor
            
        # 3. Statistical Mean Reversion (Overextended)
        elif 30.0 <= iv_rank <= 70.0:
            if rsi > 70.0: # standard overbought
                logger.info(f"VRP Trigger [{ticker}]: Stock mathematically overbought (RSI > 70).")
                return 3 # Sell Call Credit Spread
            elif rsi < 30.0: # standard oversold
                logger.info(f"VRP Trigger [{ticker}]: Stock mathematically oversold (RSI < 30).")
                return 4 # Sell Put Credit Spread
                
        return 0 # No mathematical edge

    def evaluate_exit_trail(self, ticker, time_held_hours, option_return_pct) -> tuple[bool, str]:
        """
        Evaluates extreme Options-Specific conditions without relying on directional trend lines.
        Returns (Should_Exit, Reason)
        """
        if ticker not in self.active_simulated_positions:
            return False, ""
            
        position_type = self.active_simulated_positions[ticker]['type']
        
        # 1. Profit Target Hit (Universal)
        if option_return_pct >= 25.0:
            logger.info(f"{ticker} PROFIT TARGET HIT: Option +{option_return_pct}%. Taking profit.")
            return True, "Profit Target Reached"
            
        # 2. Max Loss Stop (Universal)
        if option_return_pct <= -50.0:
            logger.info(f"{ticker} STOP LOSS HIT: Option {option_return_pct}%. Cutting losing trade.")
            return True, "Max Loss Exceeded"
            
        # 3. Aggressive Time Stop for Long Options (Straddles bleed Theta)
        if position_type == 'straddle' and time_held_hours >= 2.5 and option_return_pct < 5.0:
            logger.info(f"{ticker} THETA STOP HIT: Straddle held {time_held_hours:.1f}hrs without expansion. Cutting Theta bleed immediately.")
            return True, "Theta Time Stop"
            
        return False, ""

    def run_epoch(self):
        """
        Runs one iteration of data gathering. Looks at the universe and executes
        the R6 ORB / Trailing Stop baseline logic.
        """
        # 0. Sync Isolated Omega Global World State (NLP & Macro)
        logger.info("Syncing 14-Day Global Context exclusively for Omega Engine...")
        try:
            from omega_macro_pipeline import run_omega_macro_backfill
            run_omega_macro_backfill()
        except Exception as e:
            logger.error(f"Failed to sync Omega macro world state: {e}")
            
        tickers = self.universe.get_universe()
        
        for ticker in tickers[:3]: # Limit to first 3 for safe baseline testing
            logger.info(f"--- Processing {ticker} ---")
            
            # --- 1. Fetch State ---
            # In live production, these variables are fetched directly from Alpaca/Yahoo Market Data
            df = self.pipeline.fetch_options_chain(ticker)
            if df.empty:
                continue
                
            # Simulated Data for Architecture demonstration
            simulated_underlying_price = 505.0 
            simulated_vix = 15.0 
            simulated_iv_rank = 15.0 # Low IV (Cheap Options)
            simulated_bbw = 0.04     # Tight Bollinger Bands
            simulated_rsi = 50.0     # Neutral momentum
            simulated_vix_momentum = True # VIX is curling up
            
            # Simulated existing position state variables
            simulated_time_held = 3.5 # Hours
            simulated_pnl_pct = -5.0 # Down 5%
            
            # --- 2. Check for Exits (Trailing Stop, Theta Stop, Gamma Stop) ---
            action = -1 # Default no-op
            selected_contract = None
            
            exit_triggered, exit_reason = self.evaluate_exit_trail(
                ticker, 
                simulated_time_held,
                simulated_pnl_pct
            )
            
            if exit_triggered:
                # Action 3 represents a forced trailing stop exit.
                action = 3 
                selected_contract = self.active_simulated_positions[ticker]['contract']
                logger.info(f"Executing EXIT for {selected_contract}")
                del self.active_simulated_positions[ticker]
                
            else:
                # --- 3. Evaluate Entries (VRP Logic) ---
                target_call, target_put = self.find_target_contracts(df)
                
                # We only enter if we don't already have a position
                if ticker not in self.active_simulated_positions:
                    action = self.evaluate_entry_vrp(
                        ticker,
                        simulated_iv_rank,
                        simulated_bbw,
                        simulated_rsi,
                        simulated_vix_momentum
                    )
                    
                    if action != 0 and target_call is not None and target_put is not None:
                        # VRP Actions
                        contract_str = f"{target_call['contract_symbol']} & {target_put['contract_symbol']}"
                        
                        if action == 1:
                            strat_name = "Buy ATM Straddle"
                            pos_type = "straddle"
                        elif action == 2:
                            strat_name = "Sell OTM Iron Condor"
                            pos_type = "iron_condor"
                        elif action == 3:
                            strat_name = "Sell Call Credit Spread"
                            pos_type = "credit_spread"
                        else:
                            strat_name = "Sell Put Credit Spread"
                            pos_type = "credit_spread"
                            
                        self.active_simulated_positions[ticker] = {'type': pos_type, 'contract': contract_str}
                        logger.info(f"VRP Edge Found (IV {simulated_iv_rank}%)! Action {action} ENTRY {strat_name}: {contract_str}")
                    else:
                        action = 0 # Do nothing
                else:
                    action = 0 # Holding position, trailing stop not hit.

            # --- 4. Log the EXACT State of the world + Action taken ---
            timestamp = datetime.now().isoformat()
            self.logger_db.log_state_and_action(
                timestamp=timestamp, 
                ticker=ticker,
                underlying_price=simulated_underlying_price,
                vix=simulated_vix,
                spy_trend=simulated_ema20, # Reusing trend col for EMA 
                options_chain=df, # Logs the massive 3D surface
                action_type=action,
                selected_contract=selected_contract
            )
            
            time.sleep(1) # Rate limit protection

if __name__ == "__main__":
    actor = OmegaBaselineActor()
    logger.info("Starting up Omega Volatility Risk Premium (VRP) Engine...")
    
    # Run a few simulated epochs to show entry and holding logic
    logger.info("\n--- Epoch 1: Looking for Breakouts ---")
    actor.run_epoch()
    
    logger.info("\n--- Epoch 2: Simulating Trailing Stop Management ---")
    actor.run_epoch()
