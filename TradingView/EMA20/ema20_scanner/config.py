import os
from dataclasses import dataclass
from typing import List

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

@dataclass(frozen=True)
class Config:
    # --- TradingView export (Step 1) ---
    TV_STATE_FILE: str = os.path.join(PROJECT_ROOT, "tv_state.json")
    TV_EXPORT_ROOT: str = os.path.join(PROJECT_ROOT, "data", "tv_exports")
    TV_SCREEN_URLS: List[str] = None
    TV_HEADLESS: bool = True
    TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE: bool = True

    # --- Outputs / Data ---
    SYMBOLS_DIR: str = os.path.join(PROJECT_ROOT, "data", "symbols")
    OUTPUT_DIR: str = os.path.join(PROJECT_ROOT, "data", "outputs")

    # --- SQLite cache ---
    DB_PATH: str = os.path.join(PROJECT_ROOT, "data", "cache", "marketdata.sqlite")
    SQLITE_WAL_MODE: bool = True
    SQLITE_CACHE_DAYS_PER_SYMBOL: int = 100

    # --- Strategy settings ---
    EMA_PERIOD: int = 20
    CROSS_LOOKBACK_DAYS: int = 30       # filter: must have cross within last 30 trading days
    WINDOW_DAYS: int = 7                 # window is 7 trading days BEFORE CrossDate (CrossDate excluded)

    # Toggle: alerts allowed on CrossDate (including if CrossDate == today)
    ALLOW_ALERT_ON_CROSS_DATE: bool = True

    # Rearm mode: after an alert fires, re-arm only when price re-enters frozen window
    REARM_ON_REENTRY: bool = True

    # Re-entry definition (recommended default)
    # "strict": WindowLow < Close < WindowHigh
    # "inclusive": WindowLow <= Close <= WindowHigh
    REENTRY_MODE: str = "strict"

    # --- Backtest mode (Step 3 replay) ---
    BACKTEST_MODE: bool = True
    BACKTEST_START_DATE: str = ""   # "YYYY-MM-DD"
    BACKTEST_END_DATE: str = ""     # "YYYY-MM-DD"
    BACKTEST_SAVE_SCAN_ALL: bool = False  # WARNING: can be very large


    # Reads from DB per symbol (buffer)
    YF_READ_LIMIT_ROWS: int = 260

    def __post_init__(self):
        if self.TV_SCREEN_URLS is None:
            object.__setattr__(self, "TV_SCREEN_URLS", [
                "https://www.tradingview.com/screener/DEzUPE3I/",
            ])

CFG = Config()
