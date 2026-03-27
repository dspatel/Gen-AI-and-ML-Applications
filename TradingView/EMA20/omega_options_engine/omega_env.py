import gymnasium as gym
from gymnasium import spaces
import numpy as np
import pandas as pd
import sqlite3
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaOptionsEnv(gym.Env):
    """
    Custom OpenAI Environment for training an RL agent on the Volatility Risk Premium.
    The agent receives a 4D continuous state vector (IV Rank, BBW, RSI, VIX Momentum).
    It must choose from 5 discrete actions:
    0: Do Nothing
    1: Long Straddle
    2: Short Iron Condor
    3: Call Credit Spread
    4: Put Credit Spread
    """
    metadata = {'render_modes': ['console']}

    def __init__(self, db_path: str = "omega_telemetry.db", mode: str = "train"):
        super(OmegaOptionsEnv, self).__init__()
        
        self.db_path = db_path
        self.mode = mode
        self.df = self._load_telemetry_data()
        
        # We need a continuous historical stream to step through time
        if self.df.empty:
            raise ValueError(f"Telemetry database {db_path} is empty or missing.")
            
        self.max_steps = len(self.df) - 1
        self.current_step = 0
        
        # State Space: [IV_Rank, BBW, RSI, VIX_Momentum]
        # Using generous bounds to prevent clipping during extreme market events
        low = np.array([0.0, 0.0, 0.0, -50.0], dtype=np.float32)
        high = np.array([100.0, 1.0, 100.0, 50.0], dtype=np.float32)
        self.observation_space = spaces.Box(low=low, high=high, dtype=np.float32)
        
        # Action Space: 5 discrete choices
        self.action_space = spaces.Discrete(5)
        
        # Agent tracking
        self.active_position = None
        self.position_entry_price = 0.0
        self.position_entry_step = 0
        self.cumulative_reward = 0.0
        
    def _load_telemetry_data(self):
        """
        Extracts the state vectors that the OmegaBaselineActor generated during the 90-day backtest.
        This provides the AI with the exact mathematical landscape the math-engine saw.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            # We join the underlying state with the actions taken by the baseline to reconstruct the timeline
            query = '''
                SELECT 
                    u.timestamp, 
                    u.ticker, 
                    u.underlying_price, 
                    u.vix_value as vix_momentum, 
                    u.spy_trend as rsi,
                    a.action_type as baseline_action
                FROM underlying_state u
                LEFT JOIN engine_actions a ON u.state_id = a.state_id
                ORDER BY u.timestamp ASC
            '''
            df = pd.read_sql_query(query, conn)
            conn.close()
            
            # Since the telemetry currently only stored RSI and VIX directly in underlying_state, 
            # and IV_Rank/BBW were part of the transient pipeline, we need to synthesize them 
            # or extract them. For the Phase 13 scaffold, we will approximate the missing dimensions 
            # using the baseline's action mapping if they weren't explicitly saved as columns, 
            # but ideally, we should augment `omega_live_execution` to save all 4 metrics.
            # *Correction*: In the live script, we passed VIX Mom as `vix` and RSI as `spy_trend`.
            # To make it a true 4D state, we will synthesize IV Rank and BBW as random noise 
            # around the baseline signal thresholds for this initial environment scaffold.
            # IN PRODUCTION: Re-run historical backtester with an updated Schema to capture all 4.
            
            # Synthesize IV Rank and BBW based on known baseline constraints to bootstrap the Env
            df['iv_rank'] = np.where(df['baseline_action'].isin([2]), np.random.uniform(65, 100, len(df)), 
                            np.where(df['baseline_action'].isin([1, 3, 4]), np.random.uniform(0, 35, len(df)), 
                            np.random.uniform(35, 65, len(df))))
                            
            df['bbw'] = np.where(df['baseline_action'].isin([1, 3, 4]), np.random.uniform(0, 0.08, len(df)), 
                        np.random.uniform(0.08, 0.30, len(df)))
            
            df = df.dropna()
            logger.info(f"Loaded {len(df)} historical telemetry vectors for Reinforcement Learning.")
            return df
            
        except Exception as e:
            logger.error(f"Failed to load telemetry for RL Environment: {e}")
            return pd.DataFrame()

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        self.active_position = None
        self.cumulative_reward = 0.0
        
        return self._get_observation(), {}

    def _get_observation(self):
        row = self.df.iloc[self.current_step]
        obs = np.array([
            row['iv_rank'],
            row['bbw'],
            row['rsi'],
            row['vix_momentum']
        ], dtype=np.float32)
        return obs

    def step(self, action):
        """
        Executes the agent's chosen action against the historical data stream.
        Calculates the Reward function heavily weighting PnL vs Drawdown.
        """
        current_row = self.df.iloc[self.current_step]
        reward = 0.0
        terminated = False
        truncated = False
        info = {'ticker': current_row['ticker'], 'action': action, 'baseline_action': current_row['baseline_action']}
        
        # --- The Reward Function (Phase 13 Core) ---
        # 1. Action Penalty: Small penalty for doing nothing constantly (encourages exploration)
        if action == 0:
            reward -= 0.01 
            
        # 2. Simulated Execution PnL
        # Because we don't have full CBOE historical options pricing interconnected here,
        # we will reward the agent for successfully mimicking the highly-profitable VRP Baseline Actor
        # that we verified in Phase 11/12.
        # This is essentially "Behavioral Cloning" via RL to bootstrap the network.
        if action != 0:
            if action == current_row['baseline_action']:
                # The agent correctly identified a profitable asymmetric setup
                reward += 10.0
                info['trade_result'] = 'Win (Mimicked Baseline)'
            else:
                # The agent took a trade outside the mathematically proven edge zones (Drawdown risk)
                # Note: Iron Condors (Action 2) require high IV. If it takes Action 2 in low IV, huge penalty.
                if action == 2 and current_row['iv_rank'] < 50:
                    reward -= 15.0 # Catastrophic Vega Risk
                    info['trade_result'] = 'Catastrophic Error (Short Vega in Low IV)'
                elif action == 1 and current_row['iv_rank'] > 50:
                    reward -= 10.0 # Volatility Crush Risk
                    info['trade_result'] = 'Error (Long Vega in High IV)'
                else:
                    reward -= 2.0  # General poorly timed trade
                    info['trade_result'] = 'Sub-optimal Trade'

        self.cumulative_reward += reward
        self.current_step += 1
        
        if self.current_step >= self.max_steps:
            terminated = True
            
        next_obs = self._get_observation()
        
        return next_obs, reward, terminated, truncated, info

    def render(self):
        row = self.df.iloc[self.current_step]
        print(f"Step: {self.current_step} | Ticker: {row['ticker']} | Cum PnL Reward: {self.cumulative_reward:.2f}")

if __name__ == "__main__":
    env = OmegaOptionsEnv()
    obs, info = env.reset()
    print(f"Initial State Space: {obs}")
    print("Agent Environment Initialized Successfully.")
