import logging
from typing import List

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OmegaUniverse:
    """
    Defines the universe of highly liquid underlying assets for the Omega Options Engine.
    For options trading to be structurally viable and avoid massive bid-ask spreads, 
    we strictly filter for the most liquid ETFs and Mega-Cap tech stocks.
    """
    
    # Pre-defined list of ultra-liquid options underlyings.
    # We avoid low-volume stocks entirely because their options chains 
    # will have insurmountable bid-ask spreads.
    ULTRA_LIQUID_TICKERS = [
        "SPY",  # S&P 500 ETF - Ultimate Liquidity
        "QQQ",  # Nasdaq 100 ETF
        "IWM",  # Russell 2000 ETF
        "AAPL", # Apple
        "MSFT", # Microsoft
        "NVDA", # Nvidia
        "TSLA", # Tesla
        "META", # Meta Platforms
        "AMZN", # Amazon
        "AMD",  # Advanced Micro Devices
    ]

    def __init__(self):
        pass

    def get_universe(self) -> List[str]:
        """
        Returns the list of tickers to be tracked by the Omega Engine.
        In a more advanced state, this could dynamically screen for volume.
        """
        # Silenced logger to prevent 3x duplication across the pipeline imports
        # logger.info(f"Loaded {len(self.ULTRA_LIQUID_TICKERS)} ultra-liquid options tickers.")
        return self.ULTRA_LIQUID_TICKERS

if __name__ == "__main__":
    universe = OmegaUniverse()
    tickers = universe.get_universe()
    print(f"Omega Universe Target Tickers: {tickers}")
