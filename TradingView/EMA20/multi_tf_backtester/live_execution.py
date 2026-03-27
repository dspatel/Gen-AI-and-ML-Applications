import json
import logging
import sys
import time
from datetime import datetime
import traceback

# Setup Failsafe Telemetry Logging
logging.basicConfig(
    filename='live_production.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def send_emergency_alert(message):
    """
    Placeholder for Twilio SMS / Telegram Bot Integration.
    In a real massive failure (e.g., API down at 3:55 PM), this texts the PM.
    """
    logging.critical(f"FATAL ALERT SMS: {message}")
    print(f"\n[!!!] EMERGENCY SMS DISPATCHED: {message}\n")

def run_live_pipeline():
    """
    The orchestrator for Live execution.
    Runs the entire pipeline with strict HTTP/Data fallback loops.
    """
    logging.info("--- WAKING UP: INITIATING LIVE EOD PIPELINE ---")
    
    try:
        # Pre-Flight Check: Is the market even open today?
        from execution_engine import check_market_hours
        import requests
        
        # We can ping the live public clock without heavy auth headers
        clock_res = requests.get("https://api.alpaca.markets/v2/clock")
        if clock_res.status_code == 200:
            if not clock_res.json().get('is_open', False):
                logging.info("Market is CLOSED (Weekend/Holiday). Halting pipeline early to save 500+ API calls.")
                print("\n[HALT] Market is currently CLOSED. Halting pipeline to save API calls.\n")
                return
        # Step 1: Update Universe (Max 3 Retries)
        logging.info("Step 1: Updating Universe...")
        from universe_scraper import scrape_sp500_symbols, save_universe_to_db
        retries = 3
        for i in range(retries):
            try:
                live_targets = scrape_sp500_symbols()
                save_universe_to_db(live_targets)
                break
            except Exception as e:
                logging.error(f"Universe Scrape failed (Attempt {i+1}): {e}")
                time.sleep(5)
                if i == retries - 1: raise Exception("Failed to scrape universe after 3 retries.")
        
        # Step 2: Fetch Daily Prices via Alpaca SIP
        logging.info("Step 2: Fetching Daily Portfolio Data from Alpaca SIP...")
        from data_fetcher import fetch_and_store_daily
        import sqlite3
        import pandas as pd
        
        # Pull the universe that was just updated
        try:
            conn = sqlite3.connect('data/backtest_data.db')
            df_uni = pd.read_sql("SELECT symbol FROM active_universe", conn)
            live_targets = df_uni['symbol'].tolist()
            conn.close()
        except:
            live_targets = ['SPY']
            
        # Ensure SPY and VIX are always captured required for macro checks
        if 'SPY' not in live_targets: live_targets.append('SPY')
        
        today = pd.Timestamp('today').strftime('%Y-%m-%d')
        retries = 3
        for i in range(retries):
            try:
                # In live production, we only need the last ~200 days of data to compute the 200 SMA
                import sys
                print("") # Push a newline to separate from previous logs
                for idx, sym in enumerate(live_targets, 1):
                    fetch_and_store_daily(sym, start_date='2024-01-01', end_date=today)
                    sys.stdout.write(f"\rDownloading Alpaca SIP Data: {idx}/{len(live_targets)} symbols completed ({sym})   ")
                    sys.stdout.flush()
                print("\n[SUCCESS] Finished downloading 500+ daily bars.")
                break
            except Exception as e:
                logging.error(f"Alpaca SIP Blocked/Failed (Attempt {i+1}): {e}")
                time.sleep(10)
                if i == retries - 1: raise Exception("Data Fetch failed. Cannot generate signals.")
                
        # Step 3: Run the Math Engine for TODAY
        logging.info("Step 3: Calculating Adaptive Alpha Signals...")
        from signal_engines import PortfolioDataEngine, MacroEngine, RulesEngine
        from signal_engines import PortfolioDataEngine, MacroEngine, RulesEngine
        
        db = PortfolioDataEngine()
        db.load_all_data()
        macro = MacroEngine(db)
        rules = RulesEngine(db)
        
        # We need today's date (or the most recent market close)
        test_date = pd.to_datetime('today').normalize()
        # For safety, if we run this at 3:45PM, 'today' might not be in the DB yet if YF is delayed.
        # Fallback to the latest available date in the DB.
        available_dates = pd.to_datetime(db.daily['SPY'].index.unique()).sort_values()
        if len(available_dates) == 0:
             raise Exception("Database is completely empty.")
        test_date = available_dates[-1]
        logging.info(f"Target execution date resolved to: {test_date.date()}")
        
        weather = macro.get_weather(test_date)
        if not weather:
            logging.warning("MACRO WEATHER IS BEARISH. SYSTEM HALT. ALLOCATING TO CASH.")
            target_holdings = []
        else:
            leaderboard = rules.score_symbols(test_date)
            # In live, we take up to 10 stocks, but ONLY if they meet an absolute threshold.
            # A score > 0 means the blended 3m/6m relative strength is actively positive (growing).
            if not leaderboard.empty:
                profitable_leaders = leaderboard[leaderboard['score'] > 0]
                target_holdings = profitable_leaders.head(10)['symbol'].tolist()
            else:
                target_holdings = []
                
            logging.info(f"Macro is GREEN. Targets sorted (passing score threshold): {target_holdings}")
            
        # Collect deep state context for the target holdings AND Shadow Logger (Top 50)
        state_contexts = {}
        shadow_leaderboard = []

        if weather and 'leaderboard' in locals() and not leaderboard.empty:
            # We want to shadow log the top 50 candidates for AI training later
            top_50 = leaderboard.head(50)
            for rank, (idx, row_data) in enumerate(top_50.iterrows(), start=1):
                sym = row_data['symbol']
                score = row_data['score']
                ctx = {}
                if sym in db.daily and test_date in db.daily[sym].index:
                    try:
                        row_ctx = db.daily[sym].loc[test_date]
                        ctx = json.loads(row_ctx.to_json())
                    except Exception as e:
                        logging.warning(f"Failed to serialize state context for {sym}: {e}")
                
                # Add to shadow ledger
                shadow_leaderboard.append({
                    'symbol': sym,
                    'rank': rank,
                    'score': score,
                    'context': ctx
                })
                
                # If it's one of our actual target holdings, add to the JSON payload
                if sym in target_holdings:
                    state_contexts[sym] = ctx
            
            # Log the shadow ledger directly to SQLite
            from trade_telemetry import log_leaderboard
            log_leaderboard(test_date.date(), shadow_leaderboard)
            logging.info(f"Shadow Logging: Saved top {len(shadow_leaderboard)} candidates and their contexts to the database.")

        # Write Output to JSON for the Broker Execution Bridge
        output_file = 'live_target_portfolio.json'
        payload = {
            "timestamp": datetime.now().isoformat(),
            "target_date": str(test_date.date()),
            "macro_bullish": bool(weather),
            "target_symbols": target_holdings,
            "state_contexts": state_contexts
        }
        
        with open(output_file, 'w') as f:
            json.dump(payload, f, indent=4)
            
        logging.info(f"SUCCESS. Pipeline complete. Execution Payload written to {output_file}.")
        print(f"PIPELINE SUCCESS. Target Symbols: {target_holdings}")
            
    except Exception as fatal_e:
        error_trace = traceback.format_exc()
        logging.critical(f"PIPELINE FAILED: {fatal_e}\n{error_trace}")
        send_emergency_alert(f"Alpha Engine Pipeline Failed! Manual intervention required. Error: {str(fatal_e)}")
        sys.exit(1)

if __name__ == "__main__":
    # We must import pandas here since the script dynamically imports the engine
    import pandas as pd
    run_live_pipeline()
    
