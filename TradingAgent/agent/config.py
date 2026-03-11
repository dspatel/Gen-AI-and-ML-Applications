from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo


CHICAGO_TZ = ZoneInfo("America/Chicago")
SESSION_START = time(8, 30)
SESSION_END = time(15, 0)
OR_END = time(9, 0)
FORCED_EXIT_TIME = time(14, 50)


@dataclass(frozen=True)
class OrbConfig:
    symbol: str
    start_date: str
    end_date: str
    db_path: str = "orb_research.db"
    data_provider: str = "auto"  # auto, alpaca, synthetic
    mode: str = "orb"
    max_trades_per_day: int = 1


@dataclass(frozen=True)
class ReselectConfig:
    symbols: list[str]
    asof_date: str
    frequency: str = "monthly"  # monthly, quarterly
    side_mode: str = "long_only"  # both, long_only, short_only
    lookback_months: int = 18
    validation_months: int = 6
    min_train_trades: int = 30
    min_val_trades: int = 10
    data_provider: str = "alpaca"
    db_path: str = "orb_research.db"


@dataclass(frozen=True)
class LiveConfig:
    symbols: list[str]
    asof_date: str | None = None
    frequency: str = "monthly"  # monthly, quarterly
    side_mode: str = "long_only"  # both, long_only, short_only
    lookback_months: int = 18
    validation_months: int = 6
    min_train_trades: int = 30
    min_val_trades: int = 10
    data_provider: str = "alpaca"
    selection_data_provider: str = "alpaca"
    db_path: str = "orb_research.db"
    dry_run: bool = True
    risk_pct_per_trade: float = 0.005
    max_notional_pct: float = 0.20
    max_open_positions: int = 8
    default_equity: float = 100000.0
    force_reselect: bool = False
    default_strategy_id: str = "TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE"
    gap_entry_enabled: bool = False
    gap_entry_timeframe_min: int = 15
    gap_entry_apply_on_limit1: bool = False
    gap_entry_gap_threshold: float = 0.0015
    gap_entry_ema_dist_min: float = 0.001
    gap_entry_ema_dist_max: float = 0.012
    gap_entry_require_close_compare: bool = True
    gap_entry_require_body_direction: bool = True
    live_entry_max_age_bars: int = 1
