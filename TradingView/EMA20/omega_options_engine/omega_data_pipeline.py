import requests
import json
import os
import pandas as pd
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaDataPipeline:
    """
    Fetches real-time Options Chain and Greeks from Alpaca, 
    formatted for the World State Logger.
    """
    def __init__(self, config_path=None):
        if config_path is None:
            # Fetches keys from local environment
            config_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
        
        self.api_key, self.api_secret = self._load_credentials(config_path)
        # Alpaca Market Data Options endpoint
        self.data_url = "https://data.alpaca.markets"

    def _load_credentials(self, config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            # Use the Paper Account for options training & data fetching
            acct = next((a for a in config['accounts'] if 'Paper' in a['name']), config['accounts'][0])
            # logger.info(f"Loaded Alpaca credentials for account: {acct['name']}")
            return acct['key'], acct['secret']
        except Exception as e:
            logger.error(f"Failed to load Alpaca credentials from {config_path}: {e}")
            return None, None

    def fetch_options_chain(self, underlying_symbol: str) -> pd.DataFrame:
        """
        Fetches the complete options chain (snapshot) for a given underlying.
        Returns a DataFrame formatted for the world_state_logger.
        Requires Alpaca Options Data subscription.
        """
        if not self.api_key:
            return pd.DataFrame()

        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "accept": "application/json"
        }
        
        # Endpoint for getting latest quotes and greeks for all options of an underlying
        url = f"{self.data_url}/v1beta1/options/snapshots/{underlying_symbol}"
        
        try:
            logger.info(f"Fetching options snapshot for {underlying_symbol} from Alpaca...")
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                snapshots = data.get('snapshots', {})
                
                records = []
                for symbol, snap in snapshots.items():
                    try:
                        # Extract contract details from the standard OCC symbol
                        # E.g., SPY240719C00500000
                        exp_date_str = symbol[-15:-9]
                        opt_type = 'call' if symbol[-9] == 'C' else 'put'
                        strike = float(symbol[-8:]) / 1000.0
                        
                        exp_date = datetime.strptime(exp_date_str, "%y%m%d")
                        dte = (exp_date - datetime.now()).days
                        
                        quote = snap.get('latestQuote', {})
                        bid = quote.get('bp', 0.0)
                        ask = quote.get('ap', 0.0)
                        
                        # Note: Alpaca Options Greeks are only returned for subscribed users,
                        # and may be null if data is insufficient to calculate.
                        greeks = snap.get('impliedVolatilityAndGreeks', {})
                        if greeks is None:
                            greeks = {}

                        iv = greeks.get('impliedVolatility', 0.0)
                        # Handled missing greeks explicitly
                        delta = greeks.get('delta', 0.0) if greeks.get('delta') is not None else 0.0
                        gamma = greeks.get('gamma', 0.0) if greeks.get('gamma') is not None else 0.0
                        theta = greeks.get('theta', 0.0) if greeks.get('theta') is not None else 0.0
                        vega = greeks.get('vega', 0.0) if greeks.get('vega') is not None else 0.0
                        
                        records.append({
                            'contract_symbol': symbol,
                            'option_type': opt_type,
                            'expiration_date': exp_date.strftime("%Y-%m-%d"),
                            'dte': dte,
                            'strike': strike,
                            'bid': bid,
                            'ask': ask,
                            'implied_volatility': iv,
                            'delta': delta,
                            'gamma': gamma,
                            'theta': theta,
                            'vega': vega
                        })
                    except Exception as parse_e:
                        logger.debug(f"Error parsing OCC symbol {symbol}: {parse_e}")
                        continue
                
                df = pd.DataFrame(records)
                logger.info(f"Processed {len(df)} options contracts for {underlying_symbol}.")
                return df
            else:
                logger.error(f"Alpaca API Error {response.status_code}: {response.text}")
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"Exception fetching options data: {e}")
            return pd.DataFrame()

if __name__ == "__main__":
    pipeline = OmegaDataPipeline()
    # Test fetch for SPY (requires options data subscription)
    df = pipeline.fetch_options_chain("SPY")
    
    if not df.empty:
        print(f"\nSample Data top 5 rows:")
        print(df.head())
        print(f"\nTotal Contracts fetched: {len(df)}")
    else:
        print("\nFailed to fetch data or no active options subscription.")
