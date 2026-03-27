import requests
import json
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaExecutionEngine:
    """
    Handles options order routing to Alpaca.
    Uses Limit Orders specifically configured to capture the mid-price 
    to combat massive options bid-ask spreads.
    """
    def __init__(self, config_path=None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
        
        self.api_key, self.api_secret, self.base_url = self._load_credentials(config_path)

    def _load_credentials(self, config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            # Use the Paper Account for options training & data fetching
            acct = next((a for a in config['accounts'] if 'Paper' in a['name']), config['accounts'][0])
            logger.info(f"Execution Engine loaded Alpaca credentials for: {acct['name']}")
            return acct.get('key'), acct.get('secret'), acct.get('base_url', "https://paper-api.alpaca.markets")
        except Exception as e:
            logger.error(f"Failed to load credentials: {e}")
            return None, None, None

    def execute_mid_price_limit_order(self, symbol: str, side: str, qty: int, bid: float, ask: float):
        """
        Calculates the mid-price and sends a Limit order to Alpaca.
        For an AI training environment, capturing spread accurately is critical.
        """
        if not self.api_key:
            logger.error("No API key loaded. Cancelling order.")
            return None

        # Calculate Mid-price and round to $0.01 precision (options typically quote in pennies)
        # Note: some options trade in $0.05 increments, Alpaca generally accepts $0.01 for limits
        mid_price = round((bid + ask) / 2.0, 2)
        
        # Add slight aggressive edge depending on side (1 penny aggressive into the spread)
        # to increase fill probability without fully crossing spread
        if side.lower() == 'buy':
            limit_price = min(round(mid_price + 0.01, 2), ask)
        else:
            limit_price = max(round(mid_price - 0.01, 2), bid)

        logger.info(f"Preparing {side.upper()} order for {qty}x {symbol} @ Limit ${limit_price} (Bid: {bid}, Ask: {ask})")

        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json",
            "content-type": "application/json"
        }

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.lower(),
            "type": "limit",
            "time_in_force": "day",
            "limit_price": str(limit_price)
        }

        url = f"{self.base_url}/v2/orders"
        
        try:
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Order Successfully placed. Order ID: {data.get('id')}")
                return data
            else:
                logger.error(f"Alpaca Order Rejected [{response.status_code}]: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Execution system failed: {e}")
            return None

if __name__ == "__main__":
    # Test Initialization
    engine = OmegaExecutionEngine()
