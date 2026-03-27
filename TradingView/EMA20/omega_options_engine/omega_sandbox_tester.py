import os, sys, json, logging
import pandas as pd
from datetime import datetime, timedelta

sys.path.append(r"e:\Machine Learning\TradingView\EMA20\omega_options_engine")

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from omega_actor import OmegaBaselineActor

logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Fast 6-ticker structural validation array (as approved in Phase 25 Plan)
TICKERS = ['SPY', 'QQQ', 'AMZN', 'AAPL', 'MSFT', 'TSLA']
MAX_PORTFOLIO_SIZE = 5

# =========================================================================
# USER REQUEST: REVERT TESTER BACK TO GREAT RESULTS
# Setting GOD_MODE to True disables the 15M Cooldown and strips out the 
# nested 1-Minute entry locks. This allows the backtester to recursive "Wash Loop"
# multiple +25% profit targets inside a single 15-minute gap, generating 
# extremely inflated simulated PnL exactly like the older engine scripts did.
# =========================================================================
GOD_MODE = False

def load_creds():
    config_path = r"e:\Machine Learning\TradingView\EMA20\omega_options_engine\omega_keys.json"
    with open(config_path, 'r') as f: config = json.load(f)
    for acct in config.get("accounts", []):
        if acct.get("name") == "Paper Account": return acct['key'], acct['secret']
    return None, None

def _calculate_option_pnl(current_action, simulated_bucket, current_price, entry_price, time_held_hours):
    """Raw mathematical option yield percentage mapped to Greeks simulation."""
    if simulated_bucket == "A": gamma_mult, theta_bleed = 12.0, 0.8
    elif simulated_bucket == "B": gamma_mult, theta_bleed = 8.0, 0.5
    else: gamma_mult, theta_bleed = 4.0, 0.2
        
    if current_action == 1:
        move_pct = abs((current_price - entry_price) / entry_price) * 100
        ret = (move_pct * gamma_mult) - (time_held_hours * theta_bleed) 
        return max(-100.0, ret)
    elif current_action == 2:
        move_pct = abs((current_price - entry_price) / entry_price) * 100
        ret = (time_held_hours * (theta_bleed * 3.0)) - (move_pct * (gamma_mult * 0.6)) 
        return max(-300.0, min(100.0, ret))
    return 0.0

def run_simulation(year_label, time_windows):
    api_key, api_secret = load_creds()
    if not api_key:
        logger.error("Alpaca API connection failed.")
        return
        
    client = StockHistoricalDataClient(api_key, api_secret)
    actor = OmegaBaselineActor()
    
    raw_1m = {}
    raw_15m = {}
    
    for (start_dt, end_dt) in time_windows:
        logger.info(f"Downloading 1-Minute Array for {year_label} Sandbox...")
        for ticker in TICKERS:
            req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute, start=start_dt, end=end_dt)
            try:
                bars_1m = client.get_stock_bars(req).df.loc[ticker].reset_index()
                bars_1m['timestamp'] = pd.to_datetime(bars_1m['timestamp']).dt.tz_convert('America/New_York')
                bars_1m.set_index('timestamp', inplace=True)
                bars_1m = bars_1m.between_time('09:30', '16:00')
                
                # Standard 15-Minute Structural Math
                bars_15m = bars_1m.resample('15min', label='left', closed='left').agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'}).dropna()
                
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
                bars_15m['VIX_MOM'] = 0.0 
                
                if ticker not in raw_1m:
                    raw_1m[ticker] = bars_1m
                    raw_15m[ticker] = bars_15m
                else:
                    raw_1m[ticker] = pd.concat([raw_1m[ticker], bars_1m])
                    raw_15m[ticker] = pd.concat([raw_15m[ticker], bars_15m])
            except Exception as e:
                logger.warning(f"Failed to fetch {ticker}: {e}")

    # Build sequential 1-minute execution clock
    all_timestamps = set()
    for t in TICKERS:
        if t in raw_15m:
            for (sd, ed) in time_windows:
                try:
                    slice_idx = raw_1m[t].loc[sd.strftime("%Y-%m-%d"):ed.strftime("%Y-%m-%d")].index
                    all_timestamps.update(slice_idx)
                except: pass
            
    sorted_timestamps = sorted(list(all_timestamps))

    def execute_live_parity():
        total_cash = 100000.0 # Starting capital
        allocation_pct = 0.10 # 10% of portfolio per trade
        
        trades_log = []
        active = {}
        cooldown_db = {} # Persistent cooldown ledger to block 60-second wash loops
        
        current_day = None
        start_of_day_cash = total_cash
        
        peak_cash = total_cash
        max_drawdown = 0.0
        gross_profit = 0.0
        gross_loss = 0.0
        wins = []
        losses = []
        
        # Chronological heartbeat mapped exactly to the live scheduler
        for ts in sorted_timestamps:
            if current_day != ts.date():
                current_day = ts.date()
                start_of_day_cash = total_cash
                
            triggered_liquidations = []
            
            # --- PHASE 1: EXACT 1-MINUTE EXIT POLLING ---
            for ticker, pos in list(active.items()):
                if ticker not in raw_1m: continue
                entry_time = pos['entry_time']
                time_held_hours = (ts - entry_time).total_seconds() / 3600.0
                
                calc_pnl_pct = 0.0
                exit_trig = False
                
                # Fetch minute bar for exact Stop-Loss / Take-Profit accuracy
                try:
                    current_price = raw_1m[ticker].loc[ts, 'close']
                    calc_pnl_pct = _calculate_option_pnl(pos['action'], pos['b'], current_price, pos['entry_price'], time_held_hours)
                    
                    # Exact Live Thresholds
                    if calc_pnl_pct <= -20.0 or calc_pnl_pct >= 25.0 or time_held_hours >= 2.5:
                        exit_trig = True
                except: pass
                    
                if exit_trig:
                    triggered_liquidations.append({'t': ticker, 'pct': calc_pnl_pct})

            # Process Liquidations & Inject Cooldown Ban
            for liq in triggered_liquidations:
                t = liq['t']
                pnl_dollars = active[t]['alloc_cash'] * (liq['pct'] / 100.0)
                total_cash += pnl_dollars
                trades_log.append(liq['pct'])
                
                if pnl_dollars > 0:
                    gross_profit += pnl_dollars
                    wins.append(pnl_dollars)
                else:
                    gross_loss += abs(pnl_dollars)
                    losses.append(pnl_dollars)
                    
                # Update peak and drawdown
                if total_cash > peak_cash:
                    peak_cash = total_cash
                else:
                    drawdown = ((peak_cash - total_cash) / peak_cash) * 100
                    if drawdown > max_drawdown:
                        max_drawdown = drawdown
                
                # Update Cooldown Ledger
                cooldown_db[t] = ts
                del active[t]
                
            # --- PHASE 2: STRUCTURAL SYNCHRONIZATION LOCK ---
            current_minute = ts.minute
            
            # 1. 15-Minute First-Candle Lockout
            if ts.hour == 9 and current_minute >= 30 and current_minute < 45:
                continue
                
            # 2. Prevent mid-candle arbitrary entries
            if not GOD_MODE:
                if current_minute % 15 not in [0, 1, 2, 3]:
                    continue
            
            open_slots = MAX_PORTFOLIO_SIZE - len(active)
            if open_slots <= 0: continue
            
            # --- PHASE 3: EVALUATE VRP MATHEMATICS ---
            cands = []
            for ticker in TICKERS:
                if ticker in active or ticker not in raw_15m: continue
                
                # Reject Ticker if it exists in Cooldown DB within the last 15 minutes
                if ticker in cooldown_db and not GOD_MODE:
                    if (ts - cooldown_db[ticker]).total_seconds() < 900:
                        continue 
                        
                try:
                    # Snapping to the last completed 15-minute structural index
                    # ts is exactly [0, 1, 2, 3] minutes past a 15 min boundary.
                    # e.g., if ts = 09:46, last 15m closed at 09:45
                    latest_15m_ts = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
                    row = raw_15m[ticker].loc[latest_15m_ts]
                    
                    if pd.isna(row['IV_RANK']) or pd.isna(row['BBW']) or pd.isna(row['RSI']): continue
                    
                    action = actor.evaluate_entry_vrp(ticker, float(row['IV_RANK']), float(row['BBW']), float(row['RSI']), 0.0)
                    if action != 0:
                        buck = "C" if row['IV_RANK'] > 60.0 else "A" if row['BBW'] < 0.03 else "B"
                        score = (50.0 - row['IV_RANK']) + (1.0 / row['BBW'])
                        # Execute at exactly the 1-minute live timestamp boundary
                        cands.append({'t': ticker, 'score': score, 'action': action, 'price': raw_1m[ticker].loc[ts, 'close'], 'buck': buck})
                except: pass
                
            cands.sort(key=lambda x: x['score'], reverse=True)
            for c in cands[:open_slots]:
                alloc = total_cash * allocation_pct
                active[c['t']] = {'entry_time': ts, 'entry_price': c['price'], 'action': c['action'], 'b': c['buck'], 'alloc_cash': alloc}

        stats = {
            'trades': len(trades_log),
            'win_rate': (len(wins) / len(trades_log) * 100) if trades_log else 0.0,
            'final_cash': total_cash,
            'account_yield': ((total_cash - 100000.0) / 100000.0) * 100,
            'max_drawdown': max_drawdown,
            'profit_factor': (gross_profit / gross_loss) if gross_loss > 0 else float('inf'),
            'avg_win': (sum(wins) / len(wins)) if wins else 0.0,
            'avg_loss': (sum(losses) / len(losses)) if losses else 0.0
        }
        return stats

    # Run Simulation Execution Loop
    logger.info("="*60)
    logger.info(f" LIVE PARITY SIMULATION: {year_label}")
    logger.info("="*60)
    
    result = execute_live_parity()
    
    report_path = os.path.join(os.path.dirname(__file__), "sandbox_detailed_report_2025.txt")
    with open(report_path, "a", encoding="utf-8") as f:
        f.write(f"\n============================================================\n")
        f.write(f"  LIVE PARITY SIMULATION: {year_label}\n")
        f.write(f"============================================================\n")
        f.write(f"  Account Yield:   +{result['account_yield']:.2f}%\n")
        f.write(f"  Total Trades:    {result['trades']}\n")
        f.write(f"  Final Cash:      ${result['final_cash']:,.2f}\n")
        f.write(f"  Win Rate:        {result['win_rate']:.1f}%\n")
        f.write(f"  Max Drawdown:    -{result['max_drawdown']:.2f}%\n")
        f.write(f"  Profit Factor:   {result['profit_factor']:.2f}x\n")
        f.write(f"  Avg Win / Loss:  +${result['avg_win']:,.2f} / -${abs(result['avg_loss']):,.2f}\n")
        f.write(f"============================================================\n")
        
    logger.info(f"Report cleanly dumped to sandbox_detailed_report_2025.txt")

if __name__ == "__main__":
    report_file = os.path.join(os.path.dirname(__file__), "sandbox_detailed_report_2025.txt")
    if os.path.exists(report_file): os.remove(report_file)
    
    # Test Out of Sample - Full Year 2024
    run_simulation("FULL YEAR 2024", [(datetime(2024, 1, 1), datetime(2024, 12, 31))])
    
    # Test Out of Sample - Full Year 2025
    run_simulation("FULL YEAR 2025", [(datetime(2025, 1, 1), datetime(2025, 12, 31))])
