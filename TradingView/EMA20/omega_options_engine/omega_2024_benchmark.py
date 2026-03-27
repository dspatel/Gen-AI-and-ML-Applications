import os
import sys
import json
import logging
import traceback
from datetime import datetime
import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from omega_actor import OmegaBaselineActor

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

TICKERS = ['SPY', 'QQQ', 'NVDA', 'AAPL', 'TSLA', 'AMD', 'MSFT', 'META']
MAX_PORTFOLIO_SIZE = 5

def load_creds():
    config_path = r"e:\Machine Learning\TradingView\EMA20\omega_options_engine\omega_keys.json"
    with open(config_path, 'r') as f: config = json.load(f)
    for acct in config.get("accounts", []):
        if acct.get("name") == "Paper Account": return acct['key'], acct['secret']
    return None, None

def parallel_benchmark():
    api_key, api_secret = load_creds()
    client = StockHistoricalDataClient(api_key, api_secret)
    
    actor = OmegaBaselineActor()
    
    logger.info("Downloading massive 1-Minute payload for 2024...")
    # Add buffer for the 252 15m bar window (~10 trading days)
    start_dt = datetime(2023, 12, 10)
    end_dt = datetime(2025, 1, 1)
    
    raw_1m_data = {}
    raw_15m_data = {}
    
    for ticker in TICKERS:
        logger.info(f"Fetching {ticker} 1M bars...")
        req = StockBarsRequest(
            symbol_or_symbols=ticker, timeframe=TimeFrame.Minute,
            start=start_dt, end=end_dt
        )
        try:
            bars_1m = client.get_stock_bars(req).df.loc[ticker]
            bars_1m = bars_1m.reset_index()
            # Strict localization
            bars_1m['timestamp'] = pd.to_datetime(bars_1m['timestamp']).dt.tz_convert('America/New_York')
            bars_1m.set_index('timestamp', inplace=True)
            
            # Sub-slice strictly to market hours (09:30 to 16:00) to prevent overnight alignment mismatch
            bars_1m = bars_1m.between_time('09:30', '16:00')
            
            # Resample strictly aligned to 15m boundaries
            bars_15m = bars_1m.resample('15min', label='left', closed='left').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna()
            
            # Calculate 15m indicators perfectly mapping the Live Engine
            bars_15m['HV'] = bars_15m['close'].pct_change().rolling(window=20).std() * (252**0.5) * 100
            min_hv = bars_15m['HV'].rolling(window=252).min()
            max_hv = bars_15m['HV'].rolling(window=252).max()
            bars_15m['IV_RANK'] = ((bars_15m['HV'] - min_hv) / (max_hv - min_hv)) * 100
            bars_15m['IV_RANK'] = bars_15m['IV_RANK'].fillna(50.0)
            
            rolling_mean = bars_15m['close'].rolling(window=20).mean()
            rolling_std = bars_15m['close'].rolling(window=20).std()
            bars_15m['BBW'] = (((rolling_mean + (rolling_std * 2)) - (rolling_mean - (rolling_std * 2))) / rolling_mean)
            
            delta = bars_15m['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            bars_15m['RSI'] = 100 - (100 / (1 + rs))
            
            raw_1m_data[ticker] = bars_1m
            raw_15m_data[ticker] = bars_15m
            logger.info(f" -> {len(bars_1m)} 1m rows | {len(bars_15m)} 15m rows")
        except Exception as e:
            logger.error(f"Failed to fetch {ticker}: {e}")
            
    # Combine timestamps from the 15M frames to iterate chronologically
    all_timestamps = set()
    for t in TICKERS:
        if t in raw_15m_data:
            # We only evaluate exact 2024 dates
            slice_2024 = raw_15m_data[t].loc['2024-01-01':'2024-12-31']
            all_timestamps.update(slice_2024.index)
            
    sorted_timestamps = sorted(list(all_timestamps))
    
    # Run Two Isolated Portfolios
    def run_portfolio_sim(exit_interval="1M"):
        logger.info(f"Running Authentic Simulation: Exits = {exit_interval}")
        completed_trades = []
        active_positions = {} # symbol -> {entry_time, entry_price, action}
        
        for idx, ts in enumerate(sorted_timestamps):
            
            # 1. Evaluate Exits for currently held positions
            exits_this_step = []
            for ticker, pos in list(active_positions.items()):
                if ticker not in raw_1m_data: continue
                entry_time = pos['entry_time']
                
                # Time elapsed in total
                time_diff = ts - entry_time
                time_held_hours = time_diff.total_seconds() / 3600.0
                
                exit_triggered = False
                
                if exit_interval == "1M":
                    # Look between the last 15m check and this massive 15m check
                    prev_ts = sorted_timestamps[idx-1] if idx > 0 else entry_time
                    scan_start = max(entry_time, prev_ts)
                    scan_end = ts
                    
                    try:
                        scan_df = raw_1m_data[ticker].loc[scan_start:scan_end]
                    except:
                        scan_df = pd.DataFrame()
                        
                    for scan_ts, scan_row in scan_df.iterrows():
                        move_pct = ((scan_row['close'] - pos['entry_price']) / pos['entry_price']) * 100
                        if pos['action'] == 3: move_pct = -move_pct 
                        approx_ret = (pos['time_hours'] * 0.5) + (move_pct * 4.0) if pos['action'] in [1,3,4] else 0.0 # Approximation string
                        
                        # Simplified hard stop
                        if approx_ret <= -20.0 or approx_ret >= 50.0 or time_held_hours >= 2.5:
                            exit_triggered = True
                            pnl_captured = max(-20.0, approx_ret)
                            break
                            
                else: # 15M Exit Logic
                    try:
                        row = raw_15m_data[ticker].loc[ts]
                        move_pct = ((row['close'] - pos['entry_price']) / pos['entry_price']) * 100
                        if pos['action'] == 3: move_pct = -move_pct
                        approx_ret = (time_held_hours * 2.0) + (move_pct * 4.0)
                        
                        if approx_ret <= -20.0 or approx_ret >= 25.0 or time_held_hours >= 2.5:
                            exit_triggered = True
                            pnl_captured = approx_ret
                            
                    except: pass
                    
                if exit_triggered:
                    completed_trades.append(pnl_captured)
                    del active_positions[ticker]

            # 2. Evaluate Entries
            open_slots = MAX_PORTFOLIO_SIZE - len(active_positions)
            if open_slots <= 0: continue
            
            candidates = []
            for ticker in TICKERS:
                if ticker in active_positions: continue
                if ticker not in raw_15m_data: continue
                try:
                    row = raw_15m_data[ticker].loc[ts]
                    if pd.isna(row['IV_RANK']): continue
                    if row['IV_RANK'] < 50.0 and row['BBW'] < 0.05:
                        edge_score = (50.0 - row['IV_RANK']) + (1.0 / row['BBW'])
                        candidates.append({'ticker': ticker, 'score': edge_score, 'action': 1, 'price': row['close']})
                except KeyError: pass
                
            candidates.sort(key=lambda x: x['score'], reverse=True)
            for cand in candidates[:open_slots]:
                active_positions[cand['ticker']] = {
                    'entry_time': ts,
                    'entry_price': cand['price'],
                    'action': cand['action'],
                    'time_hours': 0
                }
                
        return completed_trades

    logger.info("Starting Port 15M Engine...")
    trades_15m = run_portfolio_sim("15M")
    logger.info("Starting Port 1M Engine...")
    trades_1m = run_portfolio_sim("1M")
    
    def report(arr):
        if not arr: return 0, 0, 0
        w = [x for x in arr if x > 0]
        return len(arr), (len(w)/len(arr))*100, sum(arr)
        
    c15, w15, p15 = report(trades_15m)
    c1, w1, p1 = report(trades_1m)

    logger.info("\n" + "="*50)
    logger.info("   2024 AUTHENTIC BENCHMARK: 1-MINUTE VS 15-MINUTE")
    logger.info("="*50)
    logger.info(f"CLASSIC 15-MINUTE EXITS:")
    logger.info(f"  Total Trades: {c15}")
    logger.info(f"  Win Rate:     {w15:.2f}%")
    logger.info(f"  Gross PnL:    {p15:.2f}%")
    logger.info("-" * 50)
    logger.info(f"LIVE ENGINE 1-MINUTE HYPER-REACTIVE EXITS:")
    logger.info(f"  Total Trades: {c1}")
    logger.info(f"  Win Rate:     {w1:.2f}%")
    logger.info(f"  Gross PnL:    {p1:.2f}%")
    logger.info("="*50)

if __name__ == "__main__":
    parallel_benchmark()
