from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from .paths import resolve_workspace_path


@dataclass(frozen=True)
class DBConfig:
    path: str


@dataclass(frozen=True)
class PathsConfig:
    research_output_dir: str
    reports_dir: str


@dataclass(frozen=True)
class SessionConfig:
    start: str
    end: str
    calendar: str


@dataclass(frozen=True)
class SymbolsConfig:
    csv_path: str


@dataclass(frozen=True)
class UnderlyingSignalConfig:
    source: str
    interval: str
    horizons: list[int]
    variant_id: str


@dataclass(frozen=True)
class OptionsConfig:
    @dataclass(frozen=True)
    class SymbolRiskOverride:
        max_premium_risk_per_trade_pct: float | None = None
        max_premium_risk_per_trade_dollars: float | None = None

    paper_account_env_prefix: str
    long_premium_only: bool
    same_day_exit_only: bool
    allowed_dte_min: int
    allowed_dte_max: int
    target_delta_min: float
    target_delta_max: float
    target_delta_preference: float
    max_spread_pct: float
    min_open_interest: int
    min_contract_volume: int
    max_contracts_per_trade: int
    max_premium_risk_per_trade_pct: float
    max_premium_risk_per_trade_dollars: float
    max_total_open_premium_pct: float
    max_total_open_premium_dollars: float
    max_symbol_open_premium_pct: float
    max_symbol_open_premium_dollars: float
    max_direction_open_premium_pct: float
    max_direction_open_premium_dollars: float
    min_cash_reserve_pct: float
    daily_loss_limit_pct: float
    max_new_trades_per_day: int
    force_exit_time: str
    symbol_risk_overrides: dict[str, SymbolRiskOverride]


@dataclass(frozen=True)
class MarketDataConfig:
    provider: str
    historical_provider: str
    stock_feed: str
    option_feed: str


@dataclass(frozen=True)
class ResearchConfig:
    @dataclass(frozen=True)
    class SplitWindow:
        start: str | None
        end: str | None

    @dataclass(frozen=True)
    class StabilityGates:
        min_train_trades: int
        min_validation_trades: int
        min_train_active_days: int
        min_validation_active_days: int
        min_train_profit_factor: float
        min_validation_profit_factor: float
        require_positive_train_pnl: bool
        require_positive_validation_pnl: bool

    baseline_mode: str
    use_ml: bool
    blind_test_locked: bool
    selection_policy: str
    train: SplitWindow
    validation: SplitWindow
    blind: SplitWindow
    stability_gates: StabilityGates


@dataclass(frozen=True)
class AppConfig:
    version: str
    status: str
    timezone: str
    db: DBConfig
    paths: PathsConfig
    session: SessionConfig
    symbols: SymbolsConfig
    underlying_signal: UnderlyingSignalConfig
    options: OptionsConfig
    market_data: MarketDataConfig
    research: ResearchConfig

    @property
    def resolved_db_path(self):
        return resolve_workspace_path(self.db.path)

    @property
    def resolved_symbols_path(self):
        return resolve_workspace_path(self.symbols.csv_path)

    @property
    def resolved_research_output_dir(self):
        return resolve_workspace_path(self.paths.research_output_dir)

    @property
    def resolved_reports_dir(self):
        return resolve_workspace_path(self.paths.reports_dir)


def _read_yaml(path: str) -> dict[str, Any]:
    p = resolve_workspace_path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Config file must deserialize to a mapping")
    return raw


def load_config(path: str = "options_r6_stable/config/config.yaml") -> AppConfig:
    raw = _read_yaml(path)
    db_cfg = raw.get("db", {}) or {}
    paths_cfg = raw.get("paths", {}) or {}
    session_cfg = raw.get("session", {}) or {}
    symbols_cfg = raw.get("symbols", {}) or {}
    signal_cfg = raw.get("underlying_signal", {}) or {}
    options_cfg = raw.get("options", {}) or {}
    md_cfg = raw.get("market_data", {}) or {}
    research_cfg = raw.get("research", {}) or {}
    split_cfg = research_cfg.get("splits", {}) or {}
    gates_cfg = research_cfg.get("stability_gates", {}) or {}
    train_cfg = split_cfg.get("train", {}) or {}
    validation_cfg = split_cfg.get("validation", {}) or {}
    blind_cfg = split_cfg.get("blind", {}) or {}

    symbol_overrides_raw = options_cfg.get("symbol_risk_overrides", {}) or {}
    symbol_risk_overrides: dict[str, OptionsConfig.SymbolRiskOverride] = {}
    if isinstance(symbol_overrides_raw, dict):
        for symbol, payload in symbol_overrides_raw.items():
            if not isinstance(payload, dict):
                continue
            symbol_risk_overrides[str(symbol).strip().upper()] = OptionsConfig.SymbolRiskOverride(
                max_premium_risk_per_trade_pct=(
                    None
                    if payload.get("max_premium_risk_per_trade_pct") in (None, "", "null")
                    else max(0.0, float(payload.get("max_premium_risk_per_trade_pct")))
                ),
                max_premium_risk_per_trade_dollars=(
                    None
                    if payload.get("max_premium_risk_per_trade_dollars") in (None, "", "null")
                    else max(0.0, float(payload.get("max_premium_risk_per_trade_dollars")))
                ),
            )

    return AppConfig(
        version=str(raw.get("version", "0.1.0")).strip(),
        status=str(raw.get("status", "design_only")).strip(),
        timezone=str(raw.get("timezone", "America/Chicago")).strip(),
        db=DBConfig(
            path=str(db_cfg.get("path", "./artifacts/options_r6_stable/options_r6_core.sqlite")).strip()
        ),
        paths=PathsConfig(
            research_output_dir=str(paths_cfg.get("research_output_dir", "./artifacts/options_r6_stable/research")).strip(),
            reports_dir=str(paths_cfg.get("reports_dir", "./artifacts/options_r6_stable/reports")).strip(),
        ),
        session=SessionConfig(
            start=str(session_cfg.get("start", "08:30")).strip(),
            end=str(session_cfg.get("end", "15:00")).strip(),
            calendar=str(session_cfg.get("calendar", "NYSE")).strip(),
        ),
        symbols=SymbolsConfig(
            csv_path=str(symbols_cfg.get("csv_path", "./options_r6_stable/config/symbols.csv")).strip()
        ),
        underlying_signal=UnderlyingSignalConfig(
            source=str(signal_cfg.get("source", "R6")).strip(),
            interval=str(signal_cfg.get("interval", "15m")).strip(),
            horizons=[int(x) for x in (signal_cfg.get("horizons") or [3, 5, 9])],
            variant_id=str(signal_cfg.get("variant_id", "R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY")).strip(),
        ),
        options=OptionsConfig(
            paper_account_env_prefix=str(options_cfg.get("paper_account_env_prefix", "ORB")).strip().upper(),
            long_premium_only=bool(options_cfg.get("long_premium_only", True)),
            same_day_exit_only=bool(options_cfg.get("same_day_exit_only", True)),
            allowed_dte_min=max(0, int(options_cfg.get("allowed_dte_min", 14))),
            allowed_dte_max=max(0, int(options_cfg.get("allowed_dte_max", 21))),
            target_delta_min=float(options_cfg.get("target_delta_min", 0.40)),
            target_delta_max=float(options_cfg.get("target_delta_max", 0.60)),
            target_delta_preference=float(options_cfg.get("target_delta_preference", 0.50)),
            max_spread_pct=max(0.0, float(options_cfg.get("max_spread_pct", 0.08))),
            min_open_interest=max(0, int(options_cfg.get("min_open_interest", 500))),
            min_contract_volume=max(0, int(options_cfg.get("min_contract_volume", 50))),
            max_contracts_per_trade=max(1, int(options_cfg.get("max_contracts_per_trade", 1))),
            max_premium_risk_per_trade_pct=max(0.0, float(options_cfg.get("max_premium_risk_per_trade_pct", 0.005))),
            max_premium_risk_per_trade_dollars=max(0.0, float(options_cfg.get("max_premium_risk_per_trade_dollars", 500.0))),
            max_total_open_premium_pct=max(0.0, float(options_cfg.get("max_total_open_premium_pct", 0.02))),
            max_total_open_premium_dollars=max(0.0, float(options_cfg.get("max_total_open_premium_dollars", 2000.0))),
            max_symbol_open_premium_pct=max(0.0, float(options_cfg.get("max_symbol_open_premium_pct", 0.01))),
            max_symbol_open_premium_dollars=max(0.0, float(options_cfg.get("max_symbol_open_premium_dollars", 1000.0))),
            max_direction_open_premium_pct=max(0.0, float(options_cfg.get("max_direction_open_premium_pct", 0.015))),
            max_direction_open_premium_dollars=max(0.0, float(options_cfg.get("max_direction_open_premium_dollars", 1500.0))),
            min_cash_reserve_pct=max(0.0, float(options_cfg.get("min_cash_reserve_pct", 0.10))),
            daily_loss_limit_pct=max(0.0, float(options_cfg.get("daily_loss_limit_pct", 0.01))),
            max_new_trades_per_day=max(1, int(options_cfg.get("max_new_trades_per_day", 6))),
            force_exit_time=str(options_cfg.get("force_exit_time", "14:50")).strip(),
            symbol_risk_overrides=symbol_risk_overrides,
        ),
        market_data=MarketDataConfig(
            provider=str(md_cfg.get("provider", "alpaca")).strip().lower(),
            historical_provider=str(md_cfg.get("historical_provider", md_cfg.get("provider", "alpaca"))).strip().lower(),
            stock_feed=str(md_cfg.get("stock_feed", "sip")).strip().lower(),
            option_feed=str(md_cfg.get("option_feed", "opra")).strip().lower(),
        ),
        research=ResearchConfig(
            baseline_mode=str(research_cfg.get("baseline_mode", "rules_only")).strip(),
            use_ml=bool(research_cfg.get("use_ml", False)),
            blind_test_locked=bool(research_cfg.get("blind_test_locked", True)),
            selection_policy=str(research_cfg.get("selection_policy", "validation_then_train")).strip(),
            train=ResearchConfig.SplitWindow(
                start=(None if train_cfg.get("start") in (None, "", "null") else str(train_cfg.get("start")).strip()),
                end=(None if train_cfg.get("end") in (None, "", "null") else str(train_cfg.get("end")).strip()),
            ),
            validation=ResearchConfig.SplitWindow(
                start=(
                    None
                    if validation_cfg.get("start") in (None, "", "null")
                    else str(validation_cfg.get("start")).strip()
                ),
                end=(
                    None
                    if validation_cfg.get("end") in (None, "", "null")
                    else str(validation_cfg.get("end")).strip()
                ),
            ),
            blind=ResearchConfig.SplitWindow(
                start=(None if blind_cfg.get("start") in (None, "", "null") else str(blind_cfg.get("start")).strip()),
                end=(None if blind_cfg.get("end") in (None, "", "null") else str(blind_cfg.get("end")).strip()),
            ),
            stability_gates=ResearchConfig.StabilityGates(
                min_train_trades=max(0, int(gates_cfg.get("min_train_trades", 20))),
                min_validation_trades=max(0, int(gates_cfg.get("min_validation_trades", 5))),
                min_train_active_days=max(0, int(gates_cfg.get("min_train_active_days", 8))),
                min_validation_active_days=max(0, int(gates_cfg.get("min_validation_active_days", 4))),
                min_train_profit_factor=max(0.0, float(gates_cfg.get("min_train_profit_factor", 1.2))),
                min_validation_profit_factor=max(0.0, float(gates_cfg.get("min_validation_profit_factor", 1.0))),
                require_positive_train_pnl=bool(gates_cfg.get("require_positive_train_pnl", True)),
                require_positive_validation_pnl=bool(gates_cfg.get("require_positive_validation_pnl", True)),
            ),
        ),
    )
