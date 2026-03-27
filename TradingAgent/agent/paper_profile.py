from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_SYMBOLS = [
    "SPY",
    "AAPL",
    "NVDA",
    "TSLA",
    "MSFT",
    "AMZN",
    "GOOGL",
    "META",
    "V",
    "ADBE",
    "AMD",
    "MA",
    "VGT",
    "VOO",
    "SCHG",
    "VTI",
]


@dataclass(frozen=True)
class PaperProfile:
    symbols: list[str]
    frequency: str = "monthly"
    side_mode: str = "long_only"
    lookback_months: int = 18
    validation_months: int = 6
    min_train_trades: int = 30
    min_val_trades: int = 10
    live_data_provider: str = "yahoo"
    live_alpaca_feed: str = "iex"
    selection_data_provider: str = "alpaca"
    risk_pct_per_trade: float = 0.005
    max_notional_pct: float = 0.20
    max_notional_dollars: float = 5000.0
    max_open_positions: int = 8
    default_equity: float = 100000.0
    default_strategy_id: str = "TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE"
    db_path: str = "orb_research.db"
    discord_enabled: bool = False
    discord_webhook_url: str = ""
    live_poll_seconds: int = 5
    live_session_calendar: str = "NYSE"
    live_wait_for_open: bool = True
    live_dashboard: bool = True
    live_dashboard_min_refresh_seconds: int = 30
    short_requires_inventory: bool = True
    gap_entry_enabled: bool = False
    gap_entry_timeframe_min: int = 15
    gap_entry_apply_on_limit1: bool = False
    gap_entry_gap_threshold: float = 0.0015
    gap_entry_ema_dist_min: float = 0.001
    gap_entry_ema_dist_max: float = 0.012
    gap_entry_require_close_compare: bool = True
    gap_entry_require_body_direction: bool = True
    live_entry_max_age_bars: int = 1


def load_paper_profile(path: str | None = None) -> PaperProfile:
    cfg = {
        "symbols": DEFAULT_SYMBOLS,
        "frequency": "monthly",
        "side_mode": "long_only",
        "lookback_months": 18,
        "validation_months": 6,
        "min_train_trades": 30,
        "min_val_trades": 10,
        "live_data_provider": "yahoo",
        "live_alpaca_feed": "iex",
        "selection_data_provider": "alpaca",
        "risk_pct_per_trade": 0.005,
        "max_notional_pct": 0.20,
        "max_notional_dollars": 5000.0,
        "max_open_positions": 8,
        "default_equity": 100000.0,
        "default_strategy_id": "TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE",
        "db_path": "orb_research.db",
        "discord_enabled": False,
        "discord_webhook_url": "",
        "live_poll_seconds": 5,
        "live_session_calendar": "NYSE",
        "live_wait_for_open": True,
        "live_dashboard": True,
        "live_dashboard_min_refresh_seconds": 30,
        "short_requires_inventory": True,
        "gap_entry_enabled": False,
        "gap_entry_timeframe_min": 15,
        "gap_entry_apply_on_limit1": False,
        "gap_entry_gap_threshold": 0.0015,
        "gap_entry_ema_dist_min": 0.001,
        "gap_entry_ema_dist_max": 0.012,
        "gap_entry_require_close_compare": True,
        "gap_entry_require_body_direction": True,
        "live_entry_max_age_bars": 1,
    }
    file_path = Path(path or "paper_profile.json")
    if file_path.exists():
        payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("paper profile JSON must be an object")
        cfg.update(payload)
    symbols = [str(s).strip().upper() for s in cfg["symbols"] if str(s).strip()]
    if not symbols:
        raise ValueError("paper profile must include at least one symbol")
    return PaperProfile(
        symbols=symbols,
        frequency=str(cfg["frequency"]).strip().lower(),
        side_mode=str(cfg["side_mode"]).strip().lower(),
        lookback_months=int(cfg["lookback_months"]),
        validation_months=int(cfg["validation_months"]),
        min_train_trades=int(cfg["min_train_trades"]),
        min_val_trades=int(cfg["min_val_trades"]),
        live_data_provider=str(cfg["live_data_provider"]).strip().lower(),
        live_alpaca_feed=str(cfg.get("live_alpaca_feed", "iex")).strip().lower() or "iex",
        selection_data_provider=str(cfg["selection_data_provider"]).strip().lower(),
        risk_pct_per_trade=float(cfg["risk_pct_per_trade"]),
        max_notional_pct=float(cfg["max_notional_pct"]),
        max_notional_dollars=float(cfg.get("max_notional_dollars", 5000.0)),
        max_open_positions=int(cfg["max_open_positions"]),
        default_equity=float(cfg["default_equity"]),
        default_strategy_id=str(cfg["default_strategy_id"]).strip(),
        db_path=str(cfg["db_path"]).strip(),
        discord_enabled=bool(cfg.get("discord_enabled", False)),
        discord_webhook_url=str(cfg.get("discord_webhook_url", "")).strip(),
        live_poll_seconds=max(1, int(cfg.get("live_poll_seconds", 5))),
        live_session_calendar=str(cfg.get("live_session_calendar", "NYSE")).strip() or "NYSE",
        live_wait_for_open=bool(cfg.get("live_wait_for_open", True)),
        live_dashboard=bool(cfg.get("live_dashboard", True)),
        live_dashboard_min_refresh_seconds=max(1, int(cfg.get("live_dashboard_min_refresh_seconds", 30))),
        short_requires_inventory=bool(cfg.get("short_requires_inventory", True)),
        gap_entry_enabled=bool(cfg.get("gap_entry_enabled", False)),
        gap_entry_timeframe_min=max(1, int(cfg.get("gap_entry_timeframe_min", 15))),
        gap_entry_apply_on_limit1=bool(cfg.get("gap_entry_apply_on_limit1", False)),
        gap_entry_gap_threshold=max(0.0, float(cfg.get("gap_entry_gap_threshold", 0.0015))),
        gap_entry_ema_dist_min=max(0.0, float(cfg.get("gap_entry_ema_dist_min", 0.001))),
        gap_entry_ema_dist_max=max(0.0, float(cfg.get("gap_entry_ema_dist_max", 0.012))),
        gap_entry_require_close_compare=bool(cfg.get("gap_entry_require_close_compare", True)),
        gap_entry_require_body_direction=bool(cfg.get("gap_entry_require_body_direction", True)),
        live_entry_max_age_bars=max(1, int(cfg.get("live_entry_max_age_bars", 1))),
    )
