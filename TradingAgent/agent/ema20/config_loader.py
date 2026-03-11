from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SymbolsConfig:
    mode: str
    single: str | None
    list: list[str]
    csv_path: str


@dataclass(frozen=True)
class SessionConfig:
    start: str
    end: str
    calendar: str


@dataclass(frozen=True)
class MarketDataConfig:
    provider: str
    interval: str
    source_r6_db_path: str
    alpaca_feed: str


@dataclass(frozen=True)
class StrategyConfig:
    primary_window_days: int
    secondary_window_days: int
    cross_lookback_days: list[int]
    monthly_flat_thresholds: list[float]
    flat_size_multiplier: float
    monthly_opposite_mode: str
    monthly_flat_mode: str
    require_daily_ema_side: bool
    require_secondary_window_confirm: bool
    chop_filter_enabled: bool
    chop_lookback_days: int
    chop_cross_count_max_values: list[int]
    allow_reentry: bool
    max_reentries_per_cycle: int
    reentry_reset_bars: int
    reentry_cooldown_bars: int
    entry_variants: list[str]
    exit_variants: list[str]
    daily_invalidation_days: list[int]
    atr_period: int
    atr_trail_multipliers: list[float]
    time_stop_days: list[int]
    retest_max_bars: int
    max_open_positions_values: list[int]
    max_new_entries_per_day_values: list[int]
    transaction_cost_bps: float
    side_mode: str
    market_regime_filter_enabled: bool
    market_regime_symbol: str
    market_regime_ema_period: int


@dataclass(frozen=True)
class AppConfig:
    version: str
    db_path: str
    research_output_dir: str
    timezone: str
    session: SessionConfig
    symbols: SymbolsConfig
    market_data: MarketDataConfig
    strategy: StrategyConfig


def _as_int_list(value: Any, default: list[int]) -> list[int]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return [int(x) for x in value]
    return [int(value)]


def _as_float_list(value: Any, default: list[float]) -> list[float]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return [float(x) for x in value]
    return [float(value)]


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, list):
        return [str(x).strip().upper() for x in value if str(x).strip()]
    text = str(value).strip().upper()
    return [text] if text else list(default)


def load_config(path: str = "ema20_stable/config.research.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p.resolve()}")

    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    version = str(raw.get("version", "1.0.0"))
    db_path = str(raw.get("db", {}).get("path", "./artifacts/ema20_stable/ema20_core.sqlite"))
    paths = raw.get("paths", {}) or {}
    research_output_dir = str(paths.get("research_output_dir", "./artifacts/ema20_stable/research"))
    timezone = str(raw.get("timezone", "America/Chicago"))

    sess = raw.get("session", {}) or {}
    session = SessionConfig(
        start=str(sess.get("start", "08:30")),
        end=str(sess.get("end", "15:00")),
        calendar=str(sess.get("calendar", "NYSE")),
    )

    sym = raw.get("symbols", {}) or {}
    symbols = SymbolsConfig(
        mode=str(sym.get("mode", "csv")).lower(),
        single=(sym.get("single") or None),
        list=[str(x).upper() for x in (sym.get("list") or [])],
        csv_path=str(sym.get("csv_path", "./ema20_stable/symbols.csv")),
    )

    md = raw.get("market_data", {}) or {}
    market_data = MarketDataConfig(
        provider=str(md.get("provider", "r6_cache")).strip().lower(),
        interval=str(md.get("interval", "15m")).lower(),
        source_r6_db_path=str(
            md.get("source_r6_db_path", paths.get("source_r6_db_path", "./artifacts/r6_stable/orb_core.sqlite"))
        ),
        alpaca_feed=str(md.get("alpaca_feed", "iex")).strip().lower(),
    )

    st = raw.get("strategy", {}) or {}
    strategy = StrategyConfig(
        primary_window_days=int(st.get("primary_window_days", 7)),
        secondary_window_days=int(st.get("secondary_window_days", 35)),
        cross_lookback_days=_as_int_list(st.get("cross_lookback_days"), [30, 45, 60, 90]),
        monthly_flat_thresholds=_as_float_list(st.get("monthly_flat_thresholds"), [0.0008, 0.0015]),
        flat_size_multiplier=float(st.get("flat_size_multiplier", 0.50)),
        monthly_opposite_mode=str(st.get("monthly_opposite_mode", "block")).strip().lower(),
        monthly_flat_mode=str(st.get("monthly_flat_mode", "reduce")).strip().lower(),
        require_daily_ema_side=bool(st.get("require_daily_ema_side", True)),
        require_secondary_window_confirm=bool(st.get("require_secondary_window_confirm", False)),
        chop_filter_enabled=bool(st.get("chop_filter_enabled", True)),
        chop_lookback_days=int(st.get("chop_lookback_days", 20)),
        chop_cross_count_max_values=_as_int_list(st.get("chop_cross_count_max_values"), [999]),
        allow_reentry=bool(st.get("allow_reentry", True)),
        max_reentries_per_cycle=int(st.get("max_reentries_per_cycle", 1)),
        reentry_reset_bars=int(st.get("reentry_reset_bars", 2)),
        reentry_cooldown_bars=int(st.get("reentry_cooldown_bars", 4)),
        entry_variants=_as_str_list(st.get("entry_variants"), ["E1", "E2", "E3"]),
        exit_variants=_as_str_list(st.get("exit_variants"), ["X1", "X2", "X3", "X4"]),
        daily_invalidation_days=_as_int_list(st.get("daily_invalidation_days"), [1, 2]),
        atr_period=int(st.get("atr_period", 14)),
        atr_trail_multipliers=_as_float_list(st.get("atr_trail_multipliers"), [2.0, 3.0]),
        time_stop_days=_as_int_list(st.get("time_stop_days"), [10, 20, 40]),
        retest_max_bars=int(st.get("retest_max_bars", 6)),
        max_open_positions_values=_as_int_list(st.get("max_open_positions_values"), [8]),
        max_new_entries_per_day_values=_as_int_list(st.get("max_new_entries_per_day_values"), [8]),
        transaction_cost_bps=float(st.get("transaction_cost_bps", 2.0)),
        side_mode=str(st.get("side_mode", "both")).strip().lower(),
        market_regime_filter_enabled=bool(st.get("market_regime_filter_enabled", False)),
        market_regime_symbol=str(st.get("market_regime_symbol", "SPY")).strip().upper(),
        market_regime_ema_period=int(st.get("market_regime_ema_period", 200)),
    )

    return AppConfig(
        version=version,
        db_path=db_path,
        research_output_dir=research_output_dir,
        timezone=timezone,
        session=session,
        symbols=symbols,
        market_data=market_data,
        strategy=strategy,
    )
