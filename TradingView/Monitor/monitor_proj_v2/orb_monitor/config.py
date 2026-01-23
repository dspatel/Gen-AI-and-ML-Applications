from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

# Optional local overrides (for secrets). Create orb_monitor/config_local.py
# and fill in DISCORD_WEBHOOK_URL / EMAIL_* values.
try:
    from .config_local import DISCORD_WEBHOOK_URL, EMAIL_FROM, EMAIL_TO, EMAIL_APP_PASSWORD
except Exception:
    DISCORD_WEBHOOK_URL = ""
    EMAIL_FROM = ""
    EMAIL_TO = ""
    EMAIL_APP_PASSWORD = ""

@dataclass
class Config:
    """
    Central configuration for the ORB monitor.

    Notes:
    - candle_minutes is intentionally restricted to Yahoo/yfinance native intervals:
      1, 2, 5, 15, 30, 60, 90
    - The opening range is ALWAYS computed from the first `orb_minutes` after market open,
      even if you start the script mid-day (catchup mode).
    """

    # Symbols
    symbols: list[str] = None  # default set in __post_init__

    # Candle sizing (Yahoo/yfinance native intraday intervals only)
    candle_minutes: int = 15
    orb_minutes: int = 30
    period: str = "5d"
    prepost: bool = False

    # Timezone/session
    tz: str = "America/Chicago"   # Chicago CST/CDT
    session_start_hm: tuple[int, int] = (8, 30)   # 8:30am Chicago
    session_end_hm: tuple[int, int] = (15, 0)     # 3:00pm Chicago

    # Strategy rules
    range_inclusive: bool = True        # inside-range check uses <= >=
    rearm_after_reentry: bool = True    # after true breakout, wait for close back in range before next
    require_2c_confirm: bool = True     # breakout candle + next candle continues direction

    # Live loop behavior
    persist_state: bool = True
    state_db_path: str = "state/orb_state.sqlite"
    grace_seconds: int = 15             # wait after bar close for data to finalize
    poll_fallback_seconds: int = 20     # fallback polling if next-close calc fails

    # Testing / after hours
    test_mode: bool = False
    test_date: str = ""                 # "YYYY-MM-DD" or "" to auto-pick latest session in data

    # Output
    output_dir: Path = Path("output")

    # Notifications
    enable_notifications: bool = True

    # Notify when the Opening Range (OR) is first established for the session.
    # This fires once per symbol per session (persisted across restarts).
    notify_on_or_creation: bool = True

    enable_discord: bool = True
    discord_webhook_url: str = DISCORD_WEBHOOK_URL       # set in config_local.py

    enable_email: bool = False          # optional Outlook SMTP
    email_from: str = EMAIL_FROM
    email_to: str = EMAIL_TO
    email_app_password: str = EMAIL_APP_PASSWORD
    smtp_host: str = "smtp.office365.com"
    smtp_port: int = 587

    # Catchup notification controls
    # If True, catchup (historical replay) TRUE breakout events will be notified to Discord/Email.
    # Default False to prevent alert spam when starting mid-session.
    send_catchup_notifications: bool = False

    # Safety valve: maximum number of catchup events to notify per symbol on startup.
    # (All catchup events are still logged to CSV regardless of this limit.)
    catchup_notify_limit_per_symbol: int = 3

    # Console
    show_dashboard: bool = True
    clear_console_each_tick: bool = True
    show_last_n_events: int = 3

    def __post_init__(self):
        if self.symbols is None:
            self.symbols = ["SPY", "TSLA", "NVDA"]

    @property
    def orb_bars(self) -> int:
        import math
        return int(math.ceil(self.orb_minutes / self.candle_minutes))
