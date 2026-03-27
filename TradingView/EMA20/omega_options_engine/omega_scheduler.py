import time
import logging
import pytz
from datetime import datetime
import os
import sys
import json
from alpaca.trading.client import TradingClient
from omega_live_execution import OmegaLiveExecutionEngine

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# User specifically requested CST timezone (America/Chicago)
CST = pytz.timezone('America/Chicago')

def check_alpaca_clock():
    """
    Queries the official Alpaca API for the exact NYSE/NASDAQ market state.
    This provides true 'Self-Awareness' for holidays via the exchange servers.
    """
    try:
        creds_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
        with open(creds_path, 'r') as f:
            data = json.load(f)
            
        paper_creds = next((a for a in data.get("accounts", []) if a.get("name") == "Paper Account"), None)
        trading_client = TradingClient(paper_creds['key'], paper_creds['secret'], paper=True)
        # Returns True if market is open right now
        return trading_client.get_clock().is_open
    except Exception as e:
        logger.error(f"Failed to query Alpaca Market Clock: {e}")
        return False

def run_ephemeral_scheduler():
    logger.info("Initializing Omega Ephemeral Sentinel (CST Market Hours: 8:30AM - 3:00PM)...")
    
    # 1. Ephemeral Self-Awareness Check (Weekends & Holidays)
    logger.info("Verifying Market Calendar with Alpaca...")
    time.sleep(10) # Give market a few seconds to officially open if launched exactly at 8:30:00 AM CST
    if not check_alpaca_clock():
        logger.info("Alpaca Server reported Market is CLOSED (Holiday or Weekend). Self-Terminating for the day.")
        sys.exit(0)
    
    logger.info("Market Verified OPEN. Exploring Options State Space...")
    
    # 2. Ephemeral Loop (Active strictly during market hours)
    while True:
        cst_now = datetime.now(CST)
        
        # 3. Market Drop-Dead Guillotine (2:45 PM CST = 3:45 PM EST)
        # Options physically trade until 4:15 PM EST, but we mandate a flat book intentionally at 3:45.
        if (cst_now.hour == 14 and cst_now.minute >= 45) or cst_now.hour > 14:
            logger.info("Market Approaching Close (3:45 PM EST). Initiating EOD Guillotine...")
            try:
                engine = OmegaLiveExecutionEngine()
                engine.liquidate_all_positions("EOD GUILLOTINE")
                
                logger.info("Awaiting 15s API Ledger Settlement Bufferr...")
                time.sleep(15) # Wait for Alpaca servers to register fills before downloading report
                
                engine.generate_eod_report()
            except Exception as e:
                logger.error(f"Failed to execute EOD Sequence: {e}")
                
            logger.info("Omega Sentinel Mission Complete. Self-Terminating.")
            sys.exit(0) # Flushes program from memory entirely.
            
        try:
            # Direct class instantiation prevents flashing CMD windows
            engine = OmegaLiveExecutionEngine()
            engine.run_live_loop()
        except Exception as e:
            logger.error(f"Omega Engine crashed during execution: {e}")
            
        # Sleep for exactly 1 minute to act as a High-Frequency Sentinel
        time.sleep(60) 

if __name__ == "__main__":
    try:
        run_ephemeral_scheduler()
    except KeyboardInterrupt:
        logger.info("Omega Sentinel Terminated by User.")
        sys.exit(0)
