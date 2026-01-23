# config.py
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class CFG:
    # =========================
    # Paths / Storage
    # =========================
    PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))

    SYMBOLS_DIR: str = os.path.join(PROJECT_ROOT, "data", "symbols")
    OUTPUT_DIR: str = os.path.join(PROJECT_ROOT, "data", "outputs")
    DB_PATH: str = os.path.join(PROJECT_ROOT, "data", "cache", "marketdata.sqlite")

    TV_EXPORT_ROOT: str = os.path.join(PROJECT_ROOT, "data", "tv_exports")

    # =========================
    # Step 1: TradingView universe download
    # =========================
    # Add your screener URLs here (one or many)
    TV_SCREEN_URLS: tuple = (
         "https://www.tradingview.com/screener/DEzUPE3I/",
    )

    TV_HEADLESS: bool = True
    TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE: bool = True

    # Root folder where TradingView exports & temp files live
    TV_EXPORT_ROOT: str = os.path.join(PROJECT_ROOT, "data", "tv_exports")

    # Playwright storage state file (logged-in TradingView session)
    # You must create this once by logging in via Playwright
    TV_STATE_FILE: str = os.path.join(TV_EXPORT_ROOT, "tv_state.json")

    # Where TradingView downloads CSVs
    TV_DOWNLOAD_DIR: str = os.path.join(TV_EXPORT_ROOT, "downloads")

    # Run browser headless
    TV_HEADLESS: bool = True

    # Delete raw TradingView CSV after parsing into symbols file
    TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE: bool = True

    # =========================
    # Step 2: SQLite / Cache
    # =========================
    SQLITE_WAL_MODE: bool = True

    # Safer default: ~1 trading year of daily rows
    SQLITE_CACHE_DAYS_PER_SYMBOL: int = 260

    # Yahoo Finance daily fetch period (buffer for EMA stability)
    YF_FETCH_PERIOD: str = "13mo"
    YF_READ_LIMIT_ROWS: int = 260

    # =========================
    # Strategy Settings
    # =========================
    EMA_PERIOD: int = 20

    # Symbol qualifies if latest EMA-touch crossover exists in last N trading days
    CROSS_LOOKBACK_DAYS: int = 30

    # Frozen windows anchored to CrossDate (exclude CrossDate candle)
    WINDOW_DAYS_SHORT: int = 7
    WINDOW_DAYS_LONG: int = 21

    # Allow evaluating breakout on the CrossDate itself
    ALLOW_ALERT_ON_CROSS_DATE: bool = True

    # Rearm after alert only when price re-enters frozen window
    REARM_ON_REENTRY: bool = True
    REENTRY_MODE: str = "strict"  # "strict" or "inclusive"

    # =========================
    # Outputs / Safety
    # =========================
    # Save eligible symbols (cross in last 30 trading days) as a daily file
    SAVE_EMA20_CROSS_SYMBOLS: bool = True

    # Protect existing non-empty alerts file from being overwritten by a 0-alert run
    PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY: bool = True

    # Ledger (dedupe LIVE + EOD)
    ENABLE_ALERTS_LEDGER: bool = True

    # =========================
    # Discord Notifications (disabled for first run)
    # =========================
    DISCORD_ENABLED: bool = True
    DISCORD_ENV: str = "PROD"  # "TEST" or "PROD"

    # For safety, load webhook from environment instead of hard-coding
    # Example (Windows PowerShell): $env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/...."
    #DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1460678446387826869/Xfm3GV5hS39UzlG7AhWgqSkYYd4pptdhav0o3Et619AHIBr1Huv5nQ6I1Y9BLQQSvnoj"

    DISCORD_SEND_LIVE_ALERTS: bool = True
    DISCORD_SEND_EOD_SUMMARY: bool = True
    DISCORD_SEND_EOD_ALERTS_TABLE: bool = True
    DISCORD_SEND_EOD_ALERTS_FILE_TEXT: bool = False  # optional: post CSV as text (can be long)
    DISCORD_MAX_ALERTS: int = 10

    # =========================
    # Testing Mode (single-day replay)
    # =========================
    TEST_MODE: bool = False
    ASOF_DATE: str = ""  # "YYYY-MM-DD" (used when TEST_MODE=True)

    # "read_only" = never writes state; "sandbox" = writes to symbol_state_test table
    TEST_STATE_MODE: str = "read_only"

    # =========================
    # Live Tracker (yfinance intraday)
    # =========================
    TIMEZONE: str = "America/Chicago"   # CST/CDT automatically
    LIVE_ENABLED: bool = True

    # Stable first-run defaults (5m bars, 5-min polling)
    LIVE_INTERVAL: str = "5m"           # "1m" or "5m"
    LIVE_POLL_SECONDS: int = 300        # 60 for faster, 300 is stable

    # Prefer the cross-universe file (ema20_cross_YYYY-MM-DD.csv) if present
    LIVE_UNIVERSE_PREFER_CROSS_FILE: bool = True

    # Session behavior (all times interpreted in TIMEZONE)
    # RTH = regular session only (recommended)
    # PRE = pre-market only, POST = after-hours only, ALL = all available
    LIVE_SESSION_MODE: str = "RTH"      # "RTH" | "PRE" | "POST" | "ALL"
    LIVE_PREMARKET_START: str = "07:00" # CST
    LIVE_POSTMARKET_END: str = "17:00"  # CST
    LIVE_AUTO_WAIT_FOR_SESSION_START: bool = True
    LIVE_AUTO_STOP_AFTER_SESSION_END: bool = True

    ALLOW_STEP1_FALLBACK_TO_LATEST_SYMBOLS = True
    LIVE_USE_LAST_COMPLETED_BAR = True


