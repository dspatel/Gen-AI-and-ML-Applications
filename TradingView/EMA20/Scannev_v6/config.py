# config.py
"""EMA20 Anchored Breakout Scanner - Configuration.

This file is intentionally *comment-heavy* so it can serve as the project's
reference guide (similar to your ORB Monitor config style).

Key ideas
---------
- We anchor signals to a *recent EMA20 range-cross* (within CROSS_LOOKBACK_DAYS).
- After the cross, we freeze pre-cross windows (Primary + optional Secondary).
- Alerts fire when price breaks the frozen window *and* is on the EMA20-confirmed side.
- LIVE uses intraday candles to fire earlier; EOD summarizes the same logic.

Environment variables (recommended)
-----------------------------------
- DISCORD_WEBHOOK_URL
    Your Discord webhook URL. We do NOT hardcode this in repo.
- EMA_DISCORD_ENABLED (0/1, true/false)
    Enables/disables all Discord messages without editing code.
- EMA_DISCORD_ENV (TEST/PROD)
    Tag messages so you know whether they came from testing.
- EMA_TEST_MODE (0/1)
    Used by daily_runner when doing ASOF scans (morning_prep) without mutating state.
- EMA_ASOF_DATE (YYYY-MM-DD)
    Force Step 3 to behave "as of" a specific trading day (for backtesting).
- EMA_TEST_STATE_MODE (read_only|sandbox)
    Controls whether Step 3 writes to SQLite in test mode.
- EMA_LIVE_SESSION_MODE (RTH|EXTENDED)
    Defines whether live monitoring includes pre/post-market.

"No confusion" outputs
----------------------
Outputs always include these explicit fields:
- PrimaryWindowDaysUsed
- SecondaryWindowDaysUsed
So you can see at a glance which configuration produced the file/alert.

"""

from __future__ import annotations

from dataclasses import dataclass
import os


def _env_bool(name: str, default: str = "0") -> bool:
    """Parse boolean env var with common truthy values."""
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_str(name: str, default: str = "") -> str:
    """Fetch string env var with whitespace trimmed."""
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class CFG:
    # ==================================================================================
    # PATHS / STORAGE
    # ==================================================================================
    # Project root (folder containing this file).
    PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))

    # Where Step 1 symbols CSVs are saved.
    SYMBOLS_DIR: str = os.path.join(PROJECT_ROOT, "data", "symbols")

    # Where Step 3 outputs (scan_all, scan_alerts) are saved.
    OUTPUT_DIR: str = os.path.join(PROJECT_ROOT, "data", "outputs")

    # SQLite DB that stores daily bars, symbol_state, and alerts_log.
    DB_PATH: str = os.path.join(PROJECT_ROOT, "data", "cache", "marketdata.sqlite")

    # TradingView export workspace (Playwright download directory + login state).
    TV_EXPORT_ROOT: str = os.path.join(PROJECT_ROOT, "data", "tv_exports")
    TV_DOWNLOAD_DIR: str = os.path.join(TV_EXPORT_ROOT, "downloads")
    TV_STATE_FILE: str = os.path.join(TV_EXPORT_ROOT, "tv_state.json")

    # ==================================================================================
    # STEP 1: TRADINGVIEW UNIVERSE DOWNLOAD
    # ==================================================================================
    # TradingView screener URLs you want to export.
    # Your screener defines: market cap >= 2B, 1M volume >= 1M, etc.
    TV_SCREEN_URLS: tuple[str, ...] = (
        "https://www.tradingview.com/screener/DEzUPE3I/",
    )

    # Run Playwright in headless mode.
    TV_HEADLESS: bool = True

    # Delete the downloaded CSV after parsing it into our project symbols file.
    # Keeps your downloads folder clean.
    TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE: bool = True

    # If Step 1 fails (e.g., TV login issues), allow using the latest symbols_*.csv
    # already in SYMBOLS_DIR (helps you keep testing).
    ALLOW_STEP1_FALLBACK_TO_LATEST_SYMBOLS: bool = True

    # ==================================================================================
    # STEP 2: YFINANCE DAILY CACHE -> SQLITE
    # ==================================================================================
    # Use WAL mode for better read/write concurrency.
    SQLITE_WAL_MODE: bool = True

    # How many daily rows per symbol we try to maintain in SQLite.
    SQLITE_CACHE_DAYS_PER_SYMBOL: int = 260  # ~1 trading year

    # yfinance period request. 13mo gives enough buffer for windows + cross lookback.
    YF_FETCH_PERIOD: str = "13mo"

    # How many rows Step 3 reads from SQLite for each symbol (for speed).
    YF_READ_LIMIT_ROWS: int = 260

    # ==================================================================================
    # STRATEGY KNOBS (MOST IMPORTANT)
    # ==================================================================================
    # EMA period for the strategy.
    EMA_PERIOD: int = 20

    # A symbol is eligible only if its most recent EMA20 range-cross occurred within
    # the last N *trading* days.
    CROSS_LOOKBACK_DAYS: int = 30

    # Primary anchored window length (trading days) used for the breakout comparison.
    # Example: 35 means we freeze the high/low of the 35 trading days BEFORE the cross.
    WINDOW_DAYS_PRIMARY: int = 35

    # Secondary window length (optional) tracked for context/metrics.
    WINDOW_DAYS_SECONDARY: int = 21

    # Whether to compute/store secondary window values.
    ENABLE_SECONDARY_WINDOW: bool = True

    # Entry conditions (applies in EOD scan and LIVE trigger):
    # LONG  when price > WindowHigh_{PRIMARY} AND price > EMA20
    # SHORT when price < WindowLow_{PRIMARY}  AND price < EMA20
    #
    # "Price" means:
    # - EOD: today's close
    # - LIVE: intraday candle close (last completed bar by default)
    ALLOW_ALERT_ON_CROSS_DATE: bool = True

    # After an alert fires, should we suppress additional alerts until price re-enters
    # the frozen primary window?
    REARM_ON_REENTRY: bool = True

    # How strict the re-entry condition is:
    # - strict: requires price to move back inside the full [low, high] window
    # - touch / close: looser alternatives (if implemented in your current version)
    REENTRY_MODE: str = "strict"

    # ==================================================================================
    # OUTPUTS / LEDGER SAFETY
    # ==================================================================================
    # Save the filtered cross-eligible symbols file (ema20_cross_YYYY-MM-DD.csv).
    SAVE_EMA20_CROSS_SYMBOLS: bool = True

    # Use alerts ledger (alerts_log table) as the source of truth.
    # This prevents duplicate alerts and prevents accidental overwrite with empty files.
    ENABLE_ALERTS_LEDGER: bool = True

    # Safeguard: if an EOD run produces 0 alerts, do NOT overwrite an existing alerts
    # file for that day.
    PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY: bool = True

    # ==================================================================================
    # DISCORD NOTIFICATIONS
    # ==================================================================================
    # Master Discord enable. Recommended: control via env var EMA_DISCORD_ENABLED.
    DISCORD_ENABLED: bool = _env_bool("EMA_DISCORD_ENABLED", "1")

    # Message environment tag (TEST/PROD). Control via env var EMA_DISCORD_ENV.
    DISCORD_ENV: str = _env_str("EMA_DISCORD_ENV", "PROD")

    # Webhook URL. Always set via env var DISCORD_WEBHOOK_URL.
    DISCORD_WEBHOOK_URL: str = _env_str("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1460678446387826869/Xfm3GV5hS39UzlG7AhWgqSkYYd4pptdhav0o3Et619AHIBr1Huv5nQ6I1Y9BLQQSvnoj")

    # Live alert pings during the session.
    DISCORD_SEND_LIVE_ALERTS: bool = True

    # EOD summary message.
    DISCORD_SEND_EOD_SUMMARY: bool = True

    # EOD top alerts table message.
    DISCORD_SEND_EOD_ALERTS_TABLE: bool = True

    # How many alerts to show in the table.
    DISCORD_MAX_ALERTS: int = 10

    # Banners help you audit what settings were used for a run.
    DISCORD_SEND_STARTUP_BANNER: bool = True
    DISCORD_SEND_EOD_BANNERS: bool = True
    DISCORD_SEND_SHUTDOWN_BANNER: bool = True

    # ==================================================================================
    # TESTING / ASOF MODE (USED BY DAILY_RUNNER)
    # ==================================================================================
    # When True, Step 3 behaves as-of ASOF_DATE and can avoid mutating persistent state.
    TEST_MODE: bool = _env_bool("EMA_TEST_MODE", "0")

    # The ASOF date in YYYY-MM-DD. If blank, Step 3 uses the latest available trading day.
    ASOF_DATE: str = _env_str("EMA_ASOF_DATE", "")

    # read_only: do not write to persistent state tables
    # sandbox: write to DB but in a "safe" way (implementation-specific)
    TEST_STATE_MODE: str = _env_str("EMA_TEST_STATE_MODE", "read_only")

    # ==================================================================================
    # LIVE TRACKER
    # ==================================================================================
    # Your local timezone (CST/CDT). Use America/Chicago to handle DST correctly.
    TIMEZONE: str = "America/Chicago"

    # Intraday interval for live monitoring.
    LIVE_INTERVAL: str = "5m"

    # Poll interval in seconds.
    LIVE_POLL_SECONDS: int = 300

    # To avoid firing on a candle that is still forming, use the last completed bar.
    LIVE_USE_LAST_COMPLETED_BAR: bool = True

    # Session scope:
    # - RTH: regular trading hours only
    # - EXTENDED: includes pre/post-market (if implemented)
    LIVE_SESSION_MODE: str = _env_str("EMA_LIVE_SESSION_MODE", "RTH")


# Convenience: instantiate once, and all scripts import CFG.
CFG = CFG()
