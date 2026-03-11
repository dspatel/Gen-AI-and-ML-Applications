"""
CANSLIM Fundamental Backtest Experiment
=========================================
Tests the 'C' in CANSLIM (Current Quarterly Earnings > 20% YoY Growth).
We avoid Look-Ahead Bias by reconstructing historical point-in-time EPS growth
using our exact earnings_history database.

We will run our Alpha Strategy logic ONLY on stocks that actively had >20% YoY EPS growth 
on the day of the trade.
"""

import pandas as pd
import numpy as np
import sqlite3
import os

from signal_engines import PortfolioDataEngine, RulesEngine, MacroEngine
from decision_engine import DecisionEngine
from universe_scraper import scrape_sp500_symbols

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'portfolio_data.db')

def build_canslim_eps_database_from_history():
    """
    Reads the EPS from the DB and calculates YoY EPS Growth.
    (Current Quarter EPS - Same Quarter Last Year EPS) / Abs(Same Quarter Last Year EPS)
    """
    if not os.path.exists(DB_PATH):
        print("Error: Database not found. Run earnings_engine.py first.")
        return None
        
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM earnings_history", conn)
    
    if df.empty:
        return None
        
    df['earnings_date'] = pd.to_datetime(df['earnings_date'])
    df = df.dropna(subset=['reported_eps'])
    df = df.sort_values(['symbol', 'earnings_date'])
    
    # Calculate YoY EPS Growth (comparing to 4 quarters ago)
    df['prev_yr_eps'] = df.groupby('symbol')['reported_eps'].shift(4)
    
    # Growth = (Current - Prev) / Abs(Prev)
    # Handle division by zero
    df['eps_yoy_growth'] = np.where(
        np.abs(df['prev_yr_eps']) > 0.01,
        (df['reported_eps'] - df['prev_yr_eps']) / np.abs(df['prev_yr_eps']),
        np.nan # Can't calculate pure % growth from 0 or negative
    )
    
    df['eps_yoy_growth_pct'] = df['eps_yoy_growth'] * 100
    
    return df

def get_allowed_canslim_universe(df_earnings, current_date, min_growth_pct=20.0):
    """
    Returns the list of symbols whose MOST RECENT earnings report 
    posted > 20% YoY EPS growth. (The 'C' in CANSLIM).
    This ensures no look-ahead bias by only querying data reported *before* the current date.
    """
    current_date = pd.to_datetime(current_date)
    past_earnings = df_earnings[df_earnings['earnings_date'] < current_date].copy()
    
    if past_earnings.empty:
        return []
        
    # Get the latest earnings report for each symbol
    latest = past_earnings.sort_values('earnings_date').groupby('symbol').last().reset_index()
    
    # Filter for > 20% growth
    canslim_winners = latest[latest['eps_yoy_growth_pct'] >= min_growth_pct]['symbol'].tolist()
    return canslim_winners

def simulate_canslim_engine(start_date='2021-01-01', end_date='2025-12-31', universe_size=50):
    print("--- INITIATING CANSLIM FUNDAMENTAL + TECHNICAL EXPERIMENT ---")
    
    # 1. Fetch Universe & Earnings
    symbols = scrape_sp500_symbols()[:universe_size]
    symbols.append('SPY')
    
    df_earnings = build_canslim_eps_database_from_history()
    if df_earnings is None:
        print("Failed to build CANSLIM EPS database.")
        return
        
    print(f"Tracking {len(symbols)} symbols.")
    
    # 2. Fetch Price Data
    data_engine = PortfolioDataEngine()
    data_engine.load_all_data()
    
    # 3. Apply Basic Technical Rules (The 'L' and 'M' of CANSLIM)
    rules_engine = RulesEngine(data_engine)
    macro_engine = MacroEngine(data_engine)
    
    # 4. Custom Backtest Loop merging fundamental gates
    print("\nRunning daily backtest simulation combining Technicals + CANSLIM Fundamentals...")
    
    if 'SPY' not in data_engine.daily:
        print("Error: SPY not in data engine.")
        return
        
    trading_days = data_engine.daily['SPY'].index.unique().sort_values()
    trading_days = trading_days[(trading_days >= start_date) & (trading_days <= end_date)]
    
    portfolio_equity = 100000.0
    equity_curve = []
    
    for i, current_date in enumerate(trading_days):
        if i == 0:
            equity_curve.append(portfolio_equity)
            continue
            
        prev_date = trading_days[i-1]
        
        # Determine Macro Regime
        is_bullish = macro_engine.get_weather(prev_date)
        if not is_bullish:
            # 100% Cash Protection (The 'M' of CANSLIM)
            equity_curve.append(portfolio_equity)
            continue
                
        # Get Candidates (Technical 'L')
        scoring_df = rules_engine.score_symbols(prev_date)
        
        if not scoring_df.empty:
            # Multi-Timeframe Filter: Only stocks > 200 SMA and > 50 SMA
            candidates = scoring_df[(scoring_df['close'] > scoring_df['sma_200']) & (scoring_df['close'] > scoring_df['sma_50'])]
            candidate_symbols = candidates['symbol'].tolist()
            
            # APPLY CANSLIM 'C' Gating (Fundamental Filter)
            canslim_approved = get_allowed_canslim_universe(df_earnings, prev_date, min_growth_pct=25.0)
            
            # Intersect
            final_targets = [s for s in candidate_symbols if s in canslim_approved]
            
            # Top 5 by Momentum Score
            if final_targets:
                final_df = candidates[candidates['symbol'].isin(final_targets)].head(5)
                final_targets = final_df['symbol'].tolist()
            
            # Simulate simple equal-weight return for the day
            if final_targets:
                daily_rets = []
                for sym in final_targets:
                    try:
                        df = data_engine.daily[sym]
                        price_prev = df.loc[prev_date, 'close']
                        price_curr = df.loc[current_date, 'close']
                        ret = (price_curr - price_prev) / price_prev
                        daily_rets.append(ret)
                    except:
                        pass
                        
                if daily_rets:
                    avg_ret = np.mean(daily_rets)
                    portfolio_equity *= (1 + avg_ret)
        
        equity_curve.append(portfolio_equity)
        
    df_performance = pd.DataFrame({'Date': trading_days, 'CANSLIM_Equity': equity_curve})
    df_performance.set_index('Date', inplace=True)
    
    total_ret = ((portfolio_equity / 100000.0) - 1) * 100
    
    # Calculate Max DD
    df_performance['Peak'] = df_performance['CANSLIM_Equity'].cummax()
    df_performance['Drawdown'] = (df_performance['CANSLIM_Equity'] - df_performance['Peak']) / df_performance['Peak']
    max_dd = df_performance['Drawdown'].min() * 100
    
    years = (len(trading_days)) / 252.0
    ann_ret = ((portfolio_equity / 100000.0) ** (1/years) - 1) * 100
    
    print("\n=== CANSLIM FUNDAMENTAL + TECHNICAL RESULTS (2021-2025) ===")
    print(f"Total Return: {total_ret:.2f}%")
    print(f"Annualized Return: {ann_ret:.2f}%")
    print(f"Maximum Drawdown: {max_dd:.2f}%")
    
    print("\nConclusion Evaluation:")
    if ann_ret > 19.9:
        print("SUCCESS! Adding the >25% YoY EPS Growth (CANSLIM 'C') Filter improved Alpha.")
    else:
        print("DEGRADED. Passing momentum trades through fundamental lagging EPS data reduced returns.")
        print("This verifies Phase 8 findings: Absolute Momentum precedes fundamental earnings reality by months.")

if __name__ == "__main__":
    simulate_canslim_engine(start_date='2021-01-01', end_date='2025-12-31', universe_size=50)
