# config.py
"""Central configuration for the EMA20 Scanner (PRODUCTION).

Key principle
-------------
**Production is not used for testing.**

- No test/replay toggles.
- No environment overrides that can silently re-route output paths.
- Runs only on trading days (runner will exit on non-trading days).

Use the separate *Scanner_TEST* project for replay/backtesting/sandbox work.
"""

from __future__ import annotations

from dataclasses import dataclass
import os


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def _join(*parts: str) -> str:
    return os.path.join(*parts)


@dataclass(frozen=True)
class CFG:
    # =========================
    # Runtime
    # =========================
    RUNTIME_ENV: str = "PROD"

    # =========================
    # Paths / Storage
    # =========================
    PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))

    SYMBOLS_DIR: str = _join(PROJECT_ROOT, "data", "symbols")
    OUTPUT_DIR: str = _join(PROJECT_ROOT, "data", "outputs")
    CACHE_DIR: str = _join(PROJECT_ROOT, "data", "cache")

    DB_PATH: str = _join(CACHE_DIR, "marketdata.sqlite")

    # Separate DB for EOD scan alerts (keeps LIVE ledger clean)
    EOD_DB_PATH: str = _join(CACHE_DIR, "eod_scan.sqlite")

    # TradingView export temp folder
    TV_EXPORT_ROOT: str = _join(PROJECT_ROOT, "data", "tv_exports")

    # =========================
    # Step 1: TradingView universe download
    # =========================
    TV_SCREEN_URLS: tuple[str, ...] = (
        "https://www.tradingview.com/screener/DEzUPE3I/",
    )

    TV_STATE_FILE: str = _join(TV_EXPORT_ROOT, "tv_state.json")
    TV_DOWNLOAD_DIR: str = _join(TV_EXPORT_ROOT, "downloads")
    TV_HEADLESS: bool = True
    TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE: bool = True

    # =========================
    # Step 2: SQLite / Cache
    # =========================
    SQLITE_WAL_MODE: bool = True

    # ~1 trading year of daily rows
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

    # EMA20 range-cross events count (feature for ML/filters)
    EMA20_CROSS_COUNT_LOOKBACK_TD: int = 30
    EMA20_CROSS_COUNT_INCLUDE_EVENT_DAY: bool = True

    # Frozen pre-cross windows
    WINDOW_DAYS_PRIMARY: int = 35
    WINDOW_DAYS_SECONDARY: int = 21
    ENABLE_SECONDARY_WINDOW: bool = True

    ALLOW_ALERT_ON_CROSS_DATE: bool = True

    REARM_ON_REENTRY: bool = True
    REENTRY_MODE: str = "strict"  # "strict" or "inclusive"

    # =========================
    # Outputs / Safety
    # =========================
    SAVE_EMA20_CROSS_SYMBOLS: bool = True

    # Protect existing non-empty alerts file from being overwritten by a 0-alert run
    PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY: bool = True

    # Ledger enabled (LIVE alerts)
    ENABLE_ALERTS_LEDGER: bool = True

    # =========================
    # Discord Notifications
    # =========================
    DISCORD_ENABLED: bool = _env_bool("EMA_DISCORD_ENABLED", "1")

    # PROD webhook URL must be provided via environment variable.
    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1460678446387826869/Xfm3GV5hS39UzlG7AhWgqSkYYd4pptdhav0o3Et619AHIBr1Huv5nQ6I1Y9BLQQSvnoj").strip()

    DISCORD_SEND_LIVE_ALERTS: bool = True
    DISCORD_SHOW_EMA20_CROSS_STATS: bool = True

    DISCORD_SEND_EOD_SUMMARY: bool = True
    DISCORD_SEND_EOD_ALERTS_TABLE: bool = True
    DISCORD_SEND_EOD_ALERTS_FILE_TEXT: bool = False
    DISCORD_SEND_EOD_ALERTS_CSV: bool = True
    DISCORD_UPLOAD_CSV_ONLY_IF_ALERTS: bool = True
    DISCORD_MAX_ALERTS: int = 10

    DISCORD_SEND_STARTUP_BANNER: bool = True
    DISCORD_SEND_SHUTDOWN_BANNER: bool = True
    DISCORD_SEND_EOD_BANNERS: bool = True

    # =========================
    # Live Tracker (yfinance intraday)
    # =========================
    TIMEZONE: str = "America/Chicago"   # CST/CDT automatically
    LIVE_ENABLED: bool = True

    LIVE_INTERVAL: str = "5m"
    LIVE_POLL_SECONDS: int = 300

    LIVE_UNIVERSE_PREFER_CROSS_FILE: bool = True

    # Session behavior (all times interpreted in TIMEZONE)
    LIVE_SESSION_MODE: str = os.getenv("EMA_LIVE_SESSION_MODE", "RTH")  # RTH | PRE | POST | ALL
    LIVE_PREMARKET_START: str = "07:00"
    LIVE_POSTMARKET_END: str = "17:00"

    LIVE_AUTO_WAIT_FOR_SESSION_START: bool = True
    LIVE_AUTO_STOP_AFTER_SESSION_END: bool = True
    LIVE_CLOSE_GRACE_MINUTES: int = 2
    LIVE_PRINT_WAIT_STATUS_EVERY_SECONDS: int = 300

    LIVE_SESSION_OPEN: str = "08:35"
    LIVE_SESSION_CLOSE: str = "15:00"

    ALLOW_STEP1_FALLBACK_TO_LATEST_SYMBOLS: bool = True
    LIVE_USE_LAST_COMPLETED_BAR: bool = True
