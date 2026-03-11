from __future__ import annotations

import argparse
import importlib.util
from collections import Counter
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_ENGINE_PATH = Path(__file__).resolve().parent.parent / "ORB_TEST" / "backtest_orb_shared_cash.py"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "data" / "market_data_cache.sqlite"
TRADING_DAYS_PER_YEAR = 252.0


def _load_orb_module(engine_path: str):
    p = Path(engine_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"ORB engine not found: {p}")
    spec = importlib.util.spec_from_file_location("orb_shared", str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _subset_bars(bars_by_symbol: dict[str, pd.DataFrame], keep_dates: set[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym, bars in bars_by_symbol.items():
        out[sym] = bars[bars["session_date"].isin(keep_dates)].reset_index(drop=True)
    return out


def _ctx_defaults(**overrides) -> dict:
    ctx = {
        "gap_abs_min": None,
        "gap_abs_max": None,
        "require_trend_above_ema20": None,
        "min_week_ret": None,
        "max_week_ret": None,
        "min_month_ret": None,
        "max_month_ret": None,
        "min_prev_vol_ratio": None,
        "max_prev_vol_ratio": None,
        "min_prev_range_pct": None,
        "max_prev_range_pct": None,
    }
    ctx.update(overrides)
    return ctx


def _build_daily_context_from_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(
            columns=[
                "session_date",
                "gap_pct",
                "trend_above_ema20",
                "week_ret",
                "month_ret",
                "prev_vol_ratio",
                "prev_range_pct",
            ]
        )

    g = bars.groupby("session_date", as_index=False).agg(
        open_day=("open", "first"),
        high_day=("high", "max"),
        low_day=("low", "min"),
        close_day=("close", "last"),
        volume_day=("volume", "sum"),
    )
    g["session_dt"] = pd.to_datetime(g["session_date"], errors="coerce")
    g = g.dropna(subset=["session_dt"]).sort_values("session_dt").reset_index(drop=True)
    if g.empty:
        return pd.DataFrame()

    g["prev_close"] = g["close_day"].shift(1)
    g["prev_high"] = g["high_day"].shift(1)
    g["prev_low"] = g["low_day"].shift(1)
    g["prev_vol"] = g["volume_day"].shift(1)

    g["ema20_daily"] = g["close_day"].ewm(span=20, adjust=False).mean()
    g["ema20_daily_prev"] = g["ema20_daily"].shift(1)
    g["trend_above_ema20"] = (g["prev_close"] > g["ema20_daily_prev"]).astype(float)

    g["week_ret"] = g["prev_close"] / g["close_day"].shift(6) - 1.0
    g["month_ret"] = g["prev_close"] / g["close_day"].shift(22) - 1.0

    g["avg_vol_20_prev"] = g["volume_day"].rolling(20, min_periods=5).mean().shift(1)
    g["prev_vol_ratio"] = g["prev_vol"] / g["avg_vol_20_prev"]

    g["gap_pct"] = g["open_day"] / g["prev_close"] - 1.0
    g["prev_range_pct"] = (g["prev_high"] - g["prev_low"]) / g["prev_close"]

    return g[
        [
            "session_date",
            "gap_pct",
            "trend_above_ema20",
            "week_ret",
            "month_ret",
            "prev_vol_ratio",
            "prev_range_pct",
        ]
    ].copy()


def _filter_dates_by_context(dates: list[str], context_df: pd.DataFrame, preset: dict) -> list[str]:
    if not dates:
        return []

    keys = [
        "gap_abs_min",
        "gap_abs_max",
        "require_trend_above_ema20",
        "min_week_ret",
        "max_week_ret",
        "min_month_ret",
        "max_month_ret",
        "min_prev_vol_ratio",
        "max_prev_vol_ratio",
        "min_prev_range_pct",
        "max_prev_range_pct",
    ]
    if not any(preset.get(k) is not None for k in keys):
        return dates
    if context_df.empty:
        return []

    work = context_df[context_df["session_date"].isin(dates)].copy()
    if work.empty:
        return []

    mask = pd.Series(True, index=work.index)

    if preset.get("gap_abs_min") is not None:
        mask &= work["gap_pct"].abs() >= float(preset["gap_abs_min"])
    if preset.get("gap_abs_max") is not None:
        mask &= work["gap_pct"].abs() <= float(preset["gap_abs_max"])

    if preset.get("require_trend_above_ema20") is not None:
        target = 1.0 if bool(preset["require_trend_above_ema20"]) else 0.0
        mask &= work["trend_above_ema20"] == target

    if preset.get("min_week_ret") is not None:
        mask &= work["week_ret"] >= float(preset["min_week_ret"])
    if preset.get("max_week_ret") is not None:
        mask &= work["week_ret"] <= float(preset["max_week_ret"])

    if preset.get("min_month_ret") is not None:
        mask &= work["month_ret"] >= float(preset["min_month_ret"])
    if preset.get("max_month_ret") is not None:
        mask &= work["month_ret"] <= float(preset["max_month_ret"])

    if preset.get("min_prev_vol_ratio") is not None:
        mask &= work["prev_vol_ratio"] >= float(preset["min_prev_vol_ratio"])
    if preset.get("max_prev_vol_ratio") is not None:
        mask &= work["prev_vol_ratio"] <= float(preset["max_prev_vol_ratio"])

    if preset.get("min_prev_range_pct") is not None:
        mask &= work["prev_range_pct"] >= float(preset["min_prev_range_pct"])
    if preset.get("max_prev_range_pct") is not None:
        mask &= work["prev_range_pct"] <= float(preset["max_prev_range_pct"])

    keep_dates = set(work.loc[mask.fillna(False), "session_date"].astype(str))
    return [d for d in dates if d in keep_dates]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Walk-forward comparison of three selection modes: "
            "global preset, symbol-specific preset, and hybrid overrides."
        )
    )
    p.add_argument("--symbols", default="QQQ,NVDA,SPY")
    p.add_argument("--symbols-file", default="")
    p.add_argument("--data-source", choices=["yfinance", "tvdatafeed", "alpaca"], default="alpaca")
    p.add_argument("--period", default="420d")
    p.add_argument("--interval", default="15m")
    p.add_argument("--tv-n-bars", type=int, default=12000)
    p.add_argument("--tv-exchanges", default="QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX")
    p.add_argument("--tv-default-exchange", default="NASDAQ")
    p.add_argument("--tv-username", default="")
    p.add_argument("--tv-password", default="")
    p.add_argument("--alpaca-key", default="")
    p.add_argument("--alpaca-secret", default="")
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex")
    p.add_argument("--alpaca-base-url", default="https://data.alpaca.markets")
    p.add_argument("--engine-path", default=str(DEFAULT_ENGINE_PATH))
    p.add_argument("--cache-db", default=str(DEFAULT_CACHE_PATH))
    p.add_argument("--cache-refresh", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--save-fold-csv", default="")
    p.add_argument("--save-summary-csv", default="")

    p.add_argument("--cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--trade-fraction", type=float, default=0.2)

    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--min-symbols", type=int, default=3)
    p.add_argument(
        "--hybrid-min-train-edge",
        type=float,
        default=50.0,
        help="Require this much train cash edge (per symbol) to override global preset in hybrid mode.",
    )
    p.add_argument("--target-min-growth-pct", type=float, default=6.0)
    p.add_argument("--target-max-growth-pct", type=float, default=7.0)
    return p.parse_args()


def _preset_pool() -> list[dict]:
    return [
        {
            "name": "baseline_close",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(),
        },
        {
            "name": "strength_015_close",
            "direction_mode": "both",
            "min_breakout_frac": 0.15,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(),
        },
        {
            "name": "rvol_13_close",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 1.3,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(),
        },
        {
            "name": "down_only_close",
            "direction_mode": "down",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(),
        },
        {
            "name": "bracket_08_12_ts3",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.8,
            "target_or_mult": 1.2,
            "time_stop_bars": 3,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(),
        },
        {
            "name": "bracket_06_12",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(),
        },
        {
            "name": "trend_up_gap_small",
            "direction_mode": "both",
            "min_breakout_frac": 0.10,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "11:00",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_max=0.012,
                require_trend_above_ema20=True,
                min_week_ret=0.0,
                min_prev_vol_ratio=0.8,
                max_prev_range_pct=0.05,
            ),
        },
        {
            "name": "trend_up_momo_close",
            "direction_mode": "both",
            "min_breakout_frac": 0.15,
            "min_rvol": 0.0,
            "use_vwap_filter": True,
            "use_ema_slope_filter": True,
            "entry_cutoff_hhmm": "11:30",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_max=0.015,
                require_trend_above_ema20=True,
                min_week_ret=0.003,
                min_month_ret=0.010,
                min_prev_vol_ratio=0.9,
            ),
        },
        {
            "name": "volume_expansion_bracket",
            "direction_mode": "both",
            "min_breakout_frac": 0.05,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.8,
            "target_or_mult": 1.5,
            "time_stop_bars": 3,
            "min_progress_r": 0.3,
            "break_even_r": 0.7,
            "trail_mode": "vwap",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_max=0.020,
                min_prev_vol_ratio=1.1,
                min_prev_range_pct=0.010,
            ),
        },
        {
            "name": "low_gap_pullback_down",
            "direction_mode": "down",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_min=0.003,
                gap_abs_max=0.020,
                require_trend_above_ema20=False,
                min_prev_range_pct=0.008,
            ),
        },
        {
            "name": "volatility_guard_close",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "close",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_max=0.015,
                require_trend_above_ema20=True,
                max_prev_range_pct=0.025,
            ),
        },
        {
            "name": "up_trend_momo_strict",
            "direction_mode": "up",
            "min_breakout_frac": 0.15,
            "min_rvol": 0.0,
            "use_vwap_filter": True,
            "use_ema_slope_filter": True,
            "entry_cutoff_hhmm": "11:00",
            "exit_mode": "bracket",
            "stop_or_mult": 0.8,
            "target_or_mult": 1.5,
            "time_stop_bars": 3,
            "min_progress_r": 0.3,
            "break_even_r": 0.7,
            "trail_mode": "vwap",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_max=0.012,
                require_trend_above_ema20=True,
                min_week_ret=0.005,
                min_month_ret=0.015,
                min_prev_vol_ratio=0.95,
                max_prev_range_pct=0.03,
            ),
        },
        {
            "name": "up_volexp_breakout",
            "direction_mode": "up",
            "min_breakout_frac": 0.10,
            "min_rvol": 1.2,
            "use_vwap_filter": False,
            "use_ema_slope_filter": True,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.6,
            "target_or_mult": 1.5,
            "time_stop_bars": 3,
            "min_progress_r": 0.3,
            "break_even_r": 0.7,
            "trail_mode": "ema",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_max=0.020,
                require_trend_above_ema20=True,
                min_prev_vol_ratio=1.1,
                min_prev_range_pct=0.008,
            ),
        },
        {
            "name": "down_trend_breakdown",
            "direction_mode": "down",
            "min_breakout_frac": 0.05,
            "min_rvol": 0.0,
            "use_vwap_filter": True,
            "use_ema_slope_filter": True,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.8,
            "target_or_mult": 1.2,
            "time_stop_bars": 3,
            "min_progress_r": 0.3,
            "break_even_r": 0.0,
            "trail_mode": "none",
            "trail_after_r": 1.0,
            **_ctx_defaults(
                gap_abs_min=0.003,
                gap_abs_max=0.02,
                require_trend_above_ema20=False,
                max_week_ret=0.01,
                max_month_ret=0.03,
                min_prev_vol_ratio=0.9,
            ),
        },
    ]


def _load_symbols_data_robust(orb, args: argparse.Namespace) -> tuple[list[str], dict[str, pd.DataFrame], list[str], list[str]]:
    symbols, tv_exchange_map = orb._resolve_symbols_and_exchange_map(args)
    available: dict[str, pd.DataFrame] = {}
    dropped: list[str] = []

    for sym in symbols:
        try:
            bars_sym, dates_sym = orb._load_market_data(
                symbols=[sym],
                period=args.period,
                interval=args.interval,
                data_source=args.data_source,
                tv_n_bars=args.tv_n_bars,
                tv_username=args.tv_username,
                tv_password=args.tv_password,
                tv_exchange_map={sym: tv_exchange_map.get(sym, args.tv_default_exchange)},
                default_exchange=args.tv_default_exchange,
                alpaca_key=args.alpaca_key,
                alpaca_secret=args.alpaca_secret,
                alpaca_feed=args.alpaca_feed,
                alpaca_base_url=args.alpaca_base_url,
                cache_db=args.cache_db,
                cache_disabled=args.no_cache,
                cache_refresh=args.cache_refresh,
            )
            if not dates_sym:
                dropped.append(sym)
                continue
            available[sym] = bars_sym[sym]
        except Exception:
            dropped.append(sym)

    good_symbols = [s for s in symbols if s in available]
    if len(good_symbols) < args.min_symbols:
        raise RuntimeError(
            f"Only {len(good_symbols)} symbols had data, below min-symbols={args.min_symbols}. "
            f"Available={good_symbols} Dropped={dropped}"
        )

    common_date_sets = [set(available[s]["session_date"].unique()) for s in good_symbols]
    common_dates = sorted(set.intersection(*common_date_sets))
    if not common_dates:
        raise RuntimeError("No common trading dates across available symbols.")

    return good_symbols, available, common_dates, dropped


def _eval_symbol_preset(
    orb,
    *,
    symbol: str,
    bars: pd.DataFrame,
    dates: list[str],
    context_df: pd.DataFrame,
    args: argparse.Namespace,
    preset: dict,
) -> float:
    filtered_dates = _filter_dates_by_context(dates=dates, context_df=context_df, preset=preset)
    if not filtered_dates:
        return 0.0

    _, sm, _ = orb.run_shared_cash_backtest(
        symbols=[symbol],
        period=args.period,
        interval=args.interval,
        start_shares_each=args.start_shares_each,
        start_cash_total=args.cash_per_symbol,
        trade_fraction=args.trade_fraction,
        data_source=args.data_source,
        tv_n_bars=args.tv_n_bars,
        tv_username=args.tv_username,
        tv_password=args.tv_password,
        tv_exchange_map={},
        tv_default_exchange=args.tv_default_exchange,
        alpaca_key=args.alpaca_key,
        alpaca_secret=args.alpaca_secret,
        alpaca_feed=args.alpaca_feed,
        alpaca_base_url=args.alpaca_base_url,
        cache_db=args.cache_db,
        cache_disabled=args.no_cache,
        cache_refresh=False,
        direction_mode=preset["direction_mode"],
        min_breakout_frac=preset["min_breakout_frac"],
        min_rvol=preset["min_rvol"],
        use_vwap_filter=preset["use_vwap_filter"],
        use_ema_slope_filter=preset["use_ema_slope_filter"],
        entry_cutoff_hhmm=preset["entry_cutoff_hhmm"],
        exit_mode=preset["exit_mode"],
        stop_or_mult=preset["stop_or_mult"],
        target_or_mult=preset["target_or_mult"],
        time_stop_bars=preset["time_stop_bars"],
        min_progress_r=preset["min_progress_r"],
        break_even_r=preset["break_even_r"],
        trail_mode=preset["trail_mode"],
        trail_after_r=preset["trail_after_r"],
        bars_by_symbol={symbol: bars},
        common_dates=filtered_dates,
    )
    return float(sm["cash_change"])


def _max_drawdown_from_changes(changes: list[float]) -> float:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for chg in changes:
        cum += float(chg)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _annualized_cash_growth_pct(total_cash_change: float, start_cash_total: float, covered_test_days: int) -> float:
    if covered_test_days <= 0 or start_cash_total <= 0:
        return float("nan")
    gross = 1.0 + (float(total_cash_change) / float(start_cash_total))
    if gross <= 0:
        return float("nan")
    return (gross ** (TRADING_DAYS_PER_YEAR / float(covered_test_days)) - 1.0) * 100.0


def _mode_summary(
    fold_df: pd.DataFrame,
    mode_col: str,
    baseline_col: str,
    start_cash_total: float,
    covered_test_days: int,
) -> dict:
    vals = fold_df[mode_col].astype(float)
    total_cash_change = float(vals.sum())
    return {
        "mode": mode_col.replace("test_cash_change_", ""),
        "total_cash_change": total_cash_change,
        "median_fold_cash_change": float(vals.median()),
        "positive_folds": int((vals > 0).sum()),
        "folds": int(len(vals)),
        "positive_rate": float((vals > 0).mean()),
        "max_drawdown": float(_max_drawdown_from_changes(vals.tolist())),
        "annualized_cash_growth_pct": _annualized_cash_growth_pct(
            total_cash_change=total_cash_change,
            start_cash_total=start_cash_total,
            covered_test_days=covered_test_days,
        ),
        "delta_vs_global_total": float(total_cash_change - fold_df[baseline_col].astype(float).sum()),
    }


def _composite_score(total_cash_change: float, max_drawdown: float, positive_rate: float) -> float:
    return float(total_cash_change - 0.35 * max_drawdown + 300.0 * positive_rate)


def main() -> None:
    args = _parse_args()
    orb = _load_orb_module(args.engine_path)
    if not args.cache_db:
        args.cache_db = str(DEFAULT_CACHE_PATH)

    symbols, bars_by_symbol, common_dates, dropped = _load_symbols_data_robust(orb, args)
    context_by_symbol = {sym: _build_daily_context_from_bars(bars_by_symbol[sym]) for sym in symbols}
    presets = _preset_pool()
    preset_by_name = {p["name"]: p for p in presets}
    preset_names = [p["name"] for p in presets]

    train_days = args.train_days
    test_days = args.test_days
    step_days = args.step_days
    start_cash_total = float(args.cash_per_symbol * len(symbols))

    rows: list[dict] = []
    all_test_dates: set[str] = set()
    global_pick_counts: Counter[str] = Counter()
    symbol_pick_counts: Counter[str] = Counter()
    hybrid_pick_counts: Counter[str] = Counter()

    i = 0
    while i + train_days + test_days <= len(common_dates):
        train_dates = common_dates[i : i + train_days]
        test_dates = common_dates[i + train_days : i + train_days + test_days]
        all_test_dates.update(test_dates)
        train_set = set(train_dates)
        test_set = set(test_dates)

        bars_train = _subset_bars(bars_by_symbol, train_set)
        bars_test = _subset_bars(bars_by_symbol, test_set)

        train_scores: dict[str, dict[str, float]] = {s: {} for s in symbols}
        test_scores: dict[str, dict[str, float]] = {s: {} for s in symbols}

        for sym in symbols:
            for pname in preset_names:
                p = preset_by_name[pname]
                train_scores[sym][pname] = _eval_symbol_preset(
                    orb,
                    symbol=sym,
                    bars=bars_train[sym],
                    dates=train_dates,
                    context_df=context_by_symbol[sym],
                    args=args,
                    preset=p,
                )
                test_scores[sym][pname] = _eval_symbol_preset(
                    orb,
                    symbol=sym,
                    bars=bars_test[sym],
                    dates=test_dates,
                    context_df=context_by_symbol[sym],
                    args=args,
                    preset=p,
                )

        global_train_by_preset = {
            pname: float(sum(train_scores[s][pname] for s in symbols)) for pname in preset_names
        }
        global_best = max(global_train_by_preset, key=global_train_by_preset.get)
        global_pick_counts[global_best] += 1

        symbol_best_by_sym: dict[str, str] = {}
        for sym in symbols:
            best_name = max(train_scores[sym], key=train_scores[sym].get)
            symbol_best_by_sym[sym] = best_name
            symbol_pick_counts[best_name] += 1

        hybrid_choice: dict[str, str] = {}
        hybrid_overrides = 0
        for sym in symbols:
            symbol_best = symbol_best_by_sym[sym]
            edge = train_scores[sym][symbol_best] - train_scores[sym][global_best]
            if edge >= args.hybrid_min_train_edge:
                hybrid_choice[sym] = symbol_best
                if symbol_best != global_best:
                    hybrid_overrides += 1
            else:
                hybrid_choice[sym] = global_best
            hybrid_pick_counts[hybrid_choice[sym]] += 1

        global_test = float(sum(test_scores[s][global_best] for s in symbols))
        symbol_test = float(sum(test_scores[s][symbol_best_by_sym[s]] for s in symbols))
        hybrid_test = float(sum(test_scores[s][hybrid_choice[s]] for s in symbols))
        baseline_test = float(sum(test_scores[s]["baseline_close"] for s in symbols))

        rows.append(
            {
                "fold": len(rows) + 1,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "global_preset": global_best,
                "hybrid_overrides": hybrid_overrides,
                "test_cash_change_global": global_test,
                "test_cash_change_symbol_specific": symbol_test,
                "test_cash_change_hybrid": hybrid_test,
                "test_cash_change_baseline_fixed": baseline_test,
            }
        )

        i += step_days

    if not rows:
        raise RuntimeError("No folds generated. Reduce train-days/test-days or increase period window.")

    fold_df = pd.DataFrame(rows)
    covered_test_days = int(len(all_test_dates))
    summary_df = pd.DataFrame(
        [
            _mode_summary(
                fold_df,
                "test_cash_change_global",
                "test_cash_change_global",
                start_cash_total=start_cash_total,
                covered_test_days=covered_test_days,
            ),
            _mode_summary(
                fold_df,
                "test_cash_change_symbol_specific",
                "test_cash_change_global",
                start_cash_total=start_cash_total,
                covered_test_days=covered_test_days,
            ),
            _mode_summary(
                fold_df,
                "test_cash_change_hybrid",
                "test_cash_change_global",
                start_cash_total=start_cash_total,
                covered_test_days=covered_test_days,
            ),
            _mode_summary(
                fold_df,
                "test_cash_change_baseline_fixed",
                "test_cash_change_global",
                start_cash_total=start_cash_total,
                covered_test_days=covered_test_days,
            ),
        ]
    )
    summary_df["meets_target_band"] = summary_df["annualized_cash_growth_pct"].between(
        args.target_min_growth_pct,
        args.target_max_growth_pct,
        inclusive="both",
    )
    summary_df["composite_score"] = summary_df.apply(
        lambda r: _composite_score(
            total_cash_change=float(r["total_cash_change"]),
            max_drawdown=float(r["max_drawdown"]),
            positive_rate=float(r["positive_rate"]),
        ),
        axis=1,
    )
    summary_df = summary_df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    wins_symbol_vs_global = int(
        (fold_df["test_cash_change_symbol_specific"] > fold_df["test_cash_change_global"]).sum()
    )
    wins_hybrid_vs_global = int((fold_df["test_cash_change_hybrid"] > fold_df["test_cash_change_global"]).sum())
    wins_baseline_vs_global = int(
        (fold_df["test_cash_change_baseline_fixed"] > fold_df["test_cash_change_global"]).sum()
    )

    print("=== Walk-Forward Compare Setup ===")
    print(
        f"symbols={len(symbols)} dropped={len(dropped)} data_source={args.data_source} "
        f"train_days={train_days} test_days={test_days} step_days={step_days}"
    )
    if dropped:
        print(f"dropped_symbols={','.join(dropped)}")
    print(f"window_start={common_dates[0]} window_end={common_dates[-1]}")
    print(f"covered_test_days={covered_test_days}")
    print(f"hybrid_min_train_edge={args.hybrid_min_train_edge:.2f}")
    print(f"target_cash_growth_band={args.target_min_growth_pct:.2f}%..{args.target_max_growth_pct:.2f}%")

    print("\n=== Fold Results ===")
    print(fold_df.to_string(index=False))

    print("\n=== Mode Summary (Sorted by Composite Score) ===")
    print(summary_df.to_string(index=False))

    folds = int(len(fold_df))
    print("\n=== Relative Win Counts vs Global Mode ===")
    print(f"symbol_specific_better_folds={wins_symbol_vs_global}/{folds}")
    print(f"hybrid_better_folds={wins_hybrid_vs_global}/{folds}")
    print(f"baseline_fixed_better_folds={wins_baseline_vs_global}/{folds}")

    print("\n=== Preset Selection Frequency ===")
    gp = pd.DataFrame(sorted(global_pick_counts.items()), columns=["preset", "global_folds"])
    sp = pd.DataFrame(sorted(symbol_pick_counts.items()), columns=["preset", "symbol_picks"])
    hp = pd.DataFrame(sorted(hybrid_pick_counts.items()), columns=["preset", "hybrid_picks"])
    freq = gp.merge(sp, on="preset", how="outer").merge(hp, on="preset", how="outer").fillna(0)
    for c in ["global_folds", "symbol_picks", "hybrid_picks"]:
        freq[c] = freq[c].astype(int)
    print(freq.sort_values(["global_folds", "symbol_picks", "hybrid_picks"], ascending=False).to_string(index=False))

    if args.save_fold_csv.strip():
        out = Path(args.save_fold_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        fold_df.to_csv(out, index=False)
        print(f"\nSaved folds CSV: {out}")
    if args.save_summary_csv.strip():
        out = Path(args.save_summary_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        summary_df.to_csv(out, index=False)
        print(f"Saved summary CSV: {out}")

    if not summary_df.empty:
        best = summary_df.iloc[0]
        print("\n=== Best Mode Recommendation ===")
        print(f"mode={best['mode']}")
        print(f"annualized_cash_growth_pct={float(best['annualized_cash_growth_pct']):.3f}")
        print(f"total_cash_change={float(best['total_cash_change']):.2f}")
        print(f"max_drawdown={float(best['max_drawdown']):.2f}")
        print(f"positive_folds={int(best['positive_folds'])}/{int(best['folds'])}")


if __name__ == "__main__":
    main()
