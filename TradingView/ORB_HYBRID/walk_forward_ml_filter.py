from __future__ import annotations

import argparse
import importlib.util
import math
from pathlib import Path
from typing import Optional

import numpy as np
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
    ]


def _build_daily_context_from_bars(bars: pd.DataFrame) -> pd.DataFrame:
    g = bars.groupby("session_date", as_index=False).agg(
        open_day=("open", "first"),
        high_day=("high", "max"),
        low_day=("low", "min"),
        close_day=("close", "last"),
        volume_day=("volume", "sum"),
    )
    g["session_dt"] = pd.to_datetime(g["session_date"], errors="coerce")
    g = g.dropna(subset=["session_dt"]).sort_values("session_dt").reset_index(drop=True)
    g["history_days"] = (g.index + 1).astype(float)

    g["prev_close"] = g["close_day"].shift(1)
    g["prev_high"] = g["high_day"].shift(1)
    g["prev_low"] = g["low_day"].shift(1)
    g["prev_vol"] = g["volume_day"].shift(1)
    g["ema20_daily_prev"] = g["close_day"].ewm(span=20, adjust=False).mean().shift(1)
    g["trend_above_ema20"] = (g["prev_close"] > g["ema20_daily_prev"]).astype(float)
    g["week_ret"] = g["prev_close"] / g["close_day"].shift(6) - 1.0
    g["month_ret"] = g["prev_close"] / g["close_day"].shift(22) - 1.0
    g["avg_vol_20_prev"] = g["volume_day"].rolling(20, min_periods=5).mean().shift(1)
    g["prev_vol_ratio"] = g["prev_vol"] / g["avg_vol_20_prev"]
    g["gap_pct"] = g["open_day"] / g["prev_close"] - 1.0
    g["prev_range_pct"] = (g["prev_high"] - g["prev_low"]) / g["prev_close"]

    def _attach_htf_prev_state(df: pd.DataFrame, rule: str, prefix: str) -> pd.DataFrame:
        tf = (
            df[["session_dt", "close_day"]]
            .set_index("session_dt")
            .resample(rule)
            .last()
            .dropna()
            .copy()
        )
        if tf.empty:
            out = df.copy()
            out[f"{prefix}_above_ema20_prev"] = np.nan
            out[f"{prefix}_cross_up_prev"] = np.nan
            out[f"{prefix}_cross_down_prev"] = np.nan
            return out

        tf[f"{prefix}_ema20"] = tf["close_day"].ewm(span=20, adjust=False).mean()
        prev_close = tf["close_day"].shift(1)
        prev_ema20 = tf[f"{prefix}_ema20"].shift(1)
        tf[f"{prefix}_above"] = (tf["close_day"] > tf[f"{prefix}_ema20"]).astype(float)
        tf[f"{prefix}_cross_up"] = ((prev_close <= prev_ema20) & (tf["close_day"] > tf[f"{prefix}_ema20"])).astype(float)
        tf[f"{prefix}_cross_down"] = ((prev_close >= prev_ema20) & (tf["close_day"] < tf[f"{prefix}_ema20"])).astype(float)

        ts = tf.index.to_numpy()
        cur = df["session_dt"].to_numpy()
        idx = np.searchsorted(ts, cur, side="left") - 1
        valid = idx >= 0

        out = df.copy()
        above_vals = tf[f"{prefix}_above"].to_numpy()
        up_vals = tf[f"{prefix}_cross_up"].to_numpy()
        down_vals = tf[f"{prefix}_cross_down"].to_numpy()

        above = np.full(len(out), np.nan)
        up = np.full(len(out), np.nan)
        down = np.full(len(out), np.nan)
        above[valid] = above_vals[idx[valid]]
        up[valid] = up_vals[idx[valid]]
        down[valid] = down_vals[idx[valid]]

        out[f"{prefix}_above_ema20_prev"] = above
        out[f"{prefix}_cross_up_prev"] = up
        out[f"{prefix}_cross_down_prev"] = down
        return out

    g = _attach_htf_prev_state(g, "W-FRI", "weekly")
    g = _attach_htf_prev_state(g, "ME", "monthly")

    return g[
        [
            "session_date",
            "gap_pct",
            "trend_above_ema20",
            "week_ret",
            "month_ret",
            "prev_vol_ratio",
            "prev_range_pct",
            "history_days",
            "weekly_above_ema20_prev",
            "weekly_cross_up_prev",
            "weekly_cross_down_prev",
            "monthly_above_ema20_prev",
            "monthly_cross_up_prev",
            "monthly_cross_down_prev",
        ]
    ].copy()


def _filter_dates_by_context(dates: list[str], context_df: pd.DataFrame, preset: dict) -> list[str]:
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

    work = context_df[context_df["session_date"].isin(dates)].copy()
    if work.empty:
        return []
    mask = pd.Series(True, index=work.index)

    if preset.get("gap_abs_min") is not None:
        mask &= work["gap_pct"].abs() >= float(preset["gap_abs_min"])
    if preset.get("gap_abs_max") is not None:
        mask &= work["gap_pct"].abs() <= float(preset["gap_abs_max"])
    if preset.get("require_trend_above_ema20") is not None:
        tgt = 1.0 if bool(preset["require_trend_above_ema20"]) else 0.0
        mask &= work["trend_above_ema20"] == tgt
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

    keep = set(work.loc[mask.fillna(False), "session_date"].astype(str))
    return [d for d in dates if d in keep]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward ML filter over ORB preset candidates.")
    p.add_argument("--engine-path", default=str(DEFAULT_ENGINE_PATH))
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
    p.add_argument("--cache-db", default=str(DEFAULT_CACHE_PATH))
    p.add_argument("--cache-refresh", action="store_true")
    p.add_argument("--no-cache", action="store_true")

    p.add_argument("--cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--trade-fraction", type=float, default=0.2)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--commission-per-share", type=float, default=0.005)
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--min-symbols", type=int, default=3)
    p.add_argument("--min-history-days", type=int, default=60)
    p.add_argument("--target-growth-pct", type=float, default=5.0)
    p.add_argument("--use-gpu", action="store_true")
    p.add_argument("--htf-gate", choices=["none", "monthly", "weekly", "both"], default="none")
    p.add_argument("--selection-mode", choices=["candidate_rank", "baseline_gate"], default="baseline_gate")
    p.add_argument("--save-fold-csv", default="")
    p.add_argument("--save-summary-csv", default="")
    return p.parse_args()


def _load_symbols_data_robust(orb, args: argparse.Namespace):
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
    good = [s for s in symbols if s in available]
    if len(good) < args.min_symbols:
        raise RuntimeError(f"Only {len(good)} symbols had data. Available={good} Dropped={dropped}")
    common_dates = sorted(set.intersection(*[set(available[s]["session_date"].unique()) for s in good]))
    return good, available, common_dates, dropped


def _collect_candidates_for_symbol(
    orb,
    symbol: str,
    bars: pd.DataFrame,
    dates: list[str],
    context_df: pd.DataFrame,
    args: argparse.Namespace,
    presets: list[dict],
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for p in presets:
        filtered_dates = _filter_dates_by_context(dates, context_df, p)
        if not filtered_dates:
            continue
        trades_df, _, _ = orb.run_shared_cash_backtest(
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
            direction_mode=p["direction_mode"],
            min_breakout_frac=p["min_breakout_frac"],
            min_rvol=p["min_rvol"],
            use_vwap_filter=p["use_vwap_filter"],
            use_ema_slope_filter=p["use_ema_slope_filter"],
            entry_cutoff_hhmm=p["entry_cutoff_hhmm"],
            exit_mode=p["exit_mode"],
            stop_or_mult=p["stop_or_mult"],
            target_or_mult=p["target_or_mult"],
            time_stop_bars=p["time_stop_bars"],
            min_progress_r=p["min_progress_r"],
            break_even_r=p["break_even_r"],
            trail_mode=p["trail_mode"],
            trail_after_r=p["trail_after_r"],
            bars_by_symbol={symbol: bars},
            common_dates=filtered_dates,
        )
        if trades_df.empty:
            continue
        t = trades_df[trades_df["quantity"] > 0].copy()
        if t.empty:
            continue
        t["preset"] = p["name"]
        rows.append(t)

    if not rows:
        return pd.DataFrame()

    out = pd.concat(rows, ignore_index=True)
    out = out.merge(context_df, on="session_date", how="left")
    out["direction_up"] = (out["direction"] == "UP_TRUE").astype(int)
    out["gap_abs"] = out["gap_pct"].abs()
    out["entry_time"] = pd.to_datetime(out["entry_time"], errors="coerce")
    out["entry_minute"] = out["entry_time"].dt.hour * 60 + out["entry_time"].dt.minute
    out["confirm_rvol"] = pd.to_numeric(out["confirm_rvol"], errors="coerce").fillna(0.0)
    out["weekly_aligned"] = (
        ((out["direction_up"] == 1) & (out["weekly_above_ema20_prev"] == 1.0))
        | ((out["direction_up"] == 0) & (out["weekly_above_ema20_prev"] == 0.0))
    ).astype(int)
    out["monthly_aligned"] = (
        ((out["direction_up"] == 1) & (out["monthly_above_ema20_prev"] == 1.0))
        | ((out["direction_up"] == 0) & (out["monthly_above_ema20_prev"] == 0.0))
    ).astype(int)
    out["both_aligned"] = ((out["weekly_aligned"] == 1) & (out["monthly_aligned"] == 1)).astype(int)
    out["edge_per_share"] = out.apply(
        lambda r: (r["exit_price"] - r["entry_price"]) if int(r["direction_up"]) == 1 else (r["entry_price"] - r["exit_price"]),
        axis=1,
    )
    out["label_win"] = (out["edge_per_share"] > 0).astype(int)
    out = out[pd.to_numeric(out["history_days"], errors="coerce").fillna(0.0) >= float(args.min_history_days)].copy()
    return out


def _apply_htf_direction_gate(cands: pd.DataFrame, gate_mode: str) -> pd.DataFrame:
    if cands.empty or gate_mode == "none":
        return cands
    if gate_mode == "monthly":
        return cands[cands["monthly_aligned"] == 1].copy()
    if gate_mode == "weekly":
        return cands[cands["weekly_aligned"] == 1].copy()
    if gate_mode == "both":
        return cands[cands["both_aligned"] == 1].copy()
    raise ValueError(f"Unsupported htf gate mode: {gate_mode}")


def _simulate_selected_candidates(
    cands: pd.DataFrame,
    symbols: list[str],
    score_col: str,
    threshold: float,
    start_cash_per_symbol: float,
    start_shares_each: int,
    trade_fraction: float,
    slippage_bps: float = 0.0,
    commission_per_share: float = 0.0,
) -> dict:
    cash = {s: float(start_cash_per_symbol) for s in symbols}
    shares = {s: int(start_shares_each) for s in symbols}
    trades_taken = 0

    if cands.empty:
        return {
            "cash_change": 0.0,
            "final_cash_total": float(start_cash_per_symbol * len(symbols)),
            "trades_taken": 0,
        }

    work = cands[cands[score_col] >= float(threshold)].copy()
    if work.empty:
        return {
            "cash_change": 0.0,
            "final_cash_total": float(start_cash_per_symbol * len(symbols)),
            "trades_taken": 0,
        }

    work["rank_key"] = work[score_col].astype(float)
    work = work.sort_values(["session_date", "symbol", "rank_key"], ascending=[True, True, False])
    chosen = work.groupby(["session_date", "symbol"], as_index=False).head(1).copy()
    chosen = chosen.sort_values(["session_date", "symbol", "entry_time"])

    slip = max(float(slippage_bps), 0.0) / 10000.0
    comm = max(float(commission_per_share), 0.0)
    for _, r in chosen.iterrows():
        sym = str(r["symbol"])
        entry = float(r["entry_price"])
        exit_px = float(r["exit_price"])
        if entry <= 0:
            continue
        if int(r["direction_up"]) == 1:
            qty = int(math.floor((cash[sym] * trade_fraction) / entry))
            if qty <= 0:
                continue
            buy_px = entry * (1.0 + slip)
            sell_px = exit_px * (1.0 - slip)
            cash[sym] += (sell_px - buy_px) * qty - (2.0 * comm * qty)
        else:
            qty = int(math.floor(shares[sym] * trade_fraction))
            if qty <= 0:
                continue
            sell_px = entry * (1.0 - slip)
            buy_px = exit_px * (1.0 + slip)
            cash[sym] += (sell_px - buy_px) * qty - (2.0 * comm * qty)
        trades_taken += 1

    final_cash_total = float(sum(cash.values()))
    start_total = float(start_cash_per_symbol * len(symbols))
    return {
        "cash_change": float(final_cash_total - start_total),
        "final_cash_total": final_cash_total,
        "trades_taken": int(trades_taken),
    }


def _train_and_score(train_df: pd.DataFrame, test_df: pd.DataFrame, use_gpu: bool) -> tuple[pd.Series, pd.Series, str]:
    feat_cols_num = [
        "direction_up",
        "entry_price",
        "or_width",
        "confirm_rvol",
        "gap_pct",
        "gap_abs",
        "week_ret",
        "month_ret",
        "prev_vol_ratio",
        "prev_range_pct",
        "entry_minute",
        "weekly_above_ema20_prev",
        "weekly_cross_up_prev",
        "weekly_cross_down_prev",
        "monthly_above_ema20_prev",
        "monthly_cross_up_prev",
        "monthly_cross_down_prev",
        "weekly_aligned",
        "monthly_aligned",
        "both_aligned",
    ]
    feat_cols_cat = ["symbol", "preset"]

    tr = train_df.copy()
    te = test_df.copy()
    for c in feat_cols_num:
        tr[c] = pd.to_numeric(tr[c], errors="coerce").fillna(0.0)
        te[c] = pd.to_numeric(te[c], errors="coerce").fillna(0.0)
    for c in feat_cols_cat:
        tr[c] = tr[c].astype(str).fillna("NA")
        te[c] = te[c].astype(str).fillna("NA")

    x_train = pd.get_dummies(tr[feat_cols_num + feat_cols_cat], columns=feat_cols_cat, dummy_na=True)
    x_test = pd.get_dummies(te[feat_cols_num + feat_cols_cat], columns=feat_cols_cat, dummy_na=True)
    x_test = x_test.reindex(columns=x_train.columns, fill_value=0)
    y_train = tr["label_win"].astype(int)

    last_xgb_error = ""
    try:
        from xgboost import XGBClassifier  # type: ignore

        devices = ["cuda", "cpu"] if use_gpu else ["cpu"]
        for dev in devices:
            try:
                model = XGBClassifier(
                    n_estimators=300,
                    max_depth=4,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    objective="binary:logistic",
                    eval_metric="logloss",
                    tree_method="hist",
                    device=dev,
                    random_state=42,
                )
                model.fit(x_train, y_train)
                p_train = pd.Series(model.predict_proba(x_train)[:, 1], index=tr.index)
                p_test = pd.Series(model.predict_proba(x_test)[:, 1], index=te.index)
                return p_train, p_test, f"xgboost_{dev}"
            except Exception as ex:
                last_xgb_error = str(ex)
    except Exception as ex:
        last_xgb_error = str(ex)

    grp = tr.groupby(["symbol", "preset", "direction_up"], as_index=False).agg(
        win_rate=("label_win", "mean"),
        mean_edge=("edge_per_share", "mean"),
    )
    global_wr = float(tr["label_win"].mean())
    edge_scale = float(max(tr["edge_per_share"].abs().median(), 1e-6))

    def _score(df: pd.DataFrame) -> pd.Series:
        z = df.merge(grp, on=["symbol", "preset", "direction_up"], how="left")
        z["win_rate"] = z["win_rate"].fillna(global_wr)
        z["mean_edge"] = z["mean_edge"].fillna(0.0)
        raw = 2.5 * (z["win_rate"] - 0.5) + (z["mean_edge"] / edge_scale)
        p = 1.0 / (1.0 + (-raw).clip(-8, 8).map(math.exp))
        return pd.Series(p.values, index=df.index)

    kind = "empirical"
    if last_xgb_error:
        kind = f"empirical_fallback:{last_xgb_error[:80]}"
    return _score(tr), _score(te), kind


def _annualized_growth(cash_change: float, start_cash_total: float, covered_test_days: int) -> float:
    if start_cash_total <= 0 or covered_test_days <= 0:
        return float("nan")
    gross = 1.0 + (cash_change / start_cash_total)
    if gross <= 0:
        return float("nan")
    return (gross ** (TRADING_DAYS_PER_YEAR / covered_test_days) - 1.0) * 100.0


def main() -> None:
    args = _parse_args()
    orb = _load_orb_module(args.engine_path)

    symbols, bars_by_symbol, common_dates, dropped = _load_symbols_data_robust(orb, args)
    presets = _preset_pool()
    preset_names = [p["name"] for p in presets]
    context_by_symbol = {s: _build_daily_context_from_bars(bars_by_symbol[s]) for s in symbols}

    rows: list[dict] = []
    all_test_dates: set[str] = set()
    model_kinds: dict[str, int] = {}

    i = 0
    while i + args.train_days + args.test_days <= len(common_dates):
        train_dates = common_dates[i : i + args.train_days]
        test_dates = common_dates[i + args.train_days : i + args.train_days + args.test_days]
        all_test_dates.update(test_dates)

        train_parts: list[pd.DataFrame] = []
        test_parts: list[pd.DataFrame] = []
        for sym in symbols:
            train_parts.append(
                _collect_candidates_for_symbol(
                    orb,
                    symbol=sym,
                    bars=bars_by_symbol[sym],
                    dates=train_dates,
                    context_df=context_by_symbol[sym],
                    args=args,
                    presets=presets,
                )
            )
            test_parts.append(
                _collect_candidates_for_symbol(
                    orb,
                    symbol=sym,
                    bars=bars_by_symbol[sym],
                    dates=test_dates,
                    context_df=context_by_symbol[sym],
                    args=args,
                    presets=presets,
                )
            )

        train_cands = pd.concat([d for d in train_parts if not d.empty], ignore_index=True) if train_parts else pd.DataFrame()
        test_cands = pd.concat([d for d in test_parts if not d.empty], ignore_index=True) if test_parts else pd.DataFrame()
        train_cands = _apply_htf_direction_gate(train_cands, args.htf_gate)
        test_cands = _apply_htf_direction_gate(test_cands, args.htf_gate)

        if train_cands.empty or test_cands.empty:
            rows.append(
                {
                    "fold": len(rows) + 1,
                    "train_start": train_dates[0],
                    "train_end": train_dates[-1],
                    "test_start": test_dates[0],
                    "test_end": test_dates[-1],
                    "baseline_global_preset": "NONE",
                    "model_kind": "none",
                    "threshold": 1.0,
                    "test_cash_change_baseline": 0.0,
                    "test_cash_change_ml": 0.0,
                    "test_trades_baseline": 0,
                    "test_trades_ml": 0,
                }
            )
            i += args.step_days
            continue

        train_cands["dummy_score"] = 1.0
        test_cands["dummy_score"] = 1.0

        best_preset = "baseline_close"
        best_train_cash = -10**18
        for pname in preset_names:
            sim = _simulate_selected_candidates(
                cands=train_cands[train_cands["preset"] == pname],
                symbols=symbols,
                score_col="dummy_score",
                threshold=0.0,
                start_cash_per_symbol=args.cash_per_symbol,
                start_shares_each=args.start_shares_each,
                trade_fraction=args.trade_fraction,
                slippage_bps=args.slippage_bps,
                commission_per_share=args.commission_per_share,
            )
            if sim["cash_change"] > best_train_cash:
                best_train_cash = sim["cash_change"]
                best_preset = pname

        baseline_test = _simulate_selected_candidates(
            cands=test_cands[test_cands["preset"] == best_preset],
            symbols=symbols,
            score_col="dummy_score",
            threshold=0.0,
            start_cash_per_symbol=args.cash_per_symbol,
            start_shares_each=args.start_shares_each,
            trade_fraction=args.trade_fraction,
            slippage_bps=args.slippage_bps,
            commission_per_share=args.commission_per_share,
        )

        if args.selection_mode == "baseline_gate":
            train_for_ml = train_cands[train_cands["preset"] == best_preset].copy()
            test_for_ml = test_cands[test_cands["preset"] == best_preset].copy()
        else:
            train_for_ml = train_cands
            test_for_ml = test_cands

        if train_for_ml.empty or test_for_ml.empty:
            model_kind = "none_baseline_passthrough"
            best_thr = 0.0
            ml_test = baseline_test
            model_kinds[model_kind] = model_kinds.get(model_kind, 0) + 1
        else:
            p_train, p_test, model_kind = _train_and_score(train_for_ml, test_for_ml, use_gpu=args.use_gpu)
            model_kinds[model_kind] = model_kinds.get(model_kind, 0) + 1
            train_for_ml["p_win"] = p_train
            test_for_ml["p_win"] = p_test

            best_thr = 0.55
            best_thr_cash = -10**18
            for thr in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
                sim = _simulate_selected_candidates(
                    cands=train_for_ml,
                    symbols=symbols,
                    score_col="p_win",
                    threshold=thr,
                    start_cash_per_symbol=args.cash_per_symbol,
                    start_shares_each=args.start_shares_each,
                    trade_fraction=args.trade_fraction,
                    slippage_bps=args.slippage_bps,
                    commission_per_share=args.commission_per_share,
                )
                if sim["cash_change"] > best_thr_cash:
                    best_thr_cash = sim["cash_change"]
                    best_thr = thr

            ml_test = _simulate_selected_candidates(
                cands=test_for_ml,
                symbols=symbols,
                score_col="p_win",
                threshold=best_thr,
                start_cash_per_symbol=args.cash_per_symbol,
                start_shares_each=args.start_shares_each,
                trade_fraction=args.trade_fraction,
                slippage_bps=args.slippage_bps,
                commission_per_share=args.commission_per_share,
            )

        rows.append(
            {
                "fold": len(rows) + 1,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "baseline_global_preset": best_preset,
                "model_kind": model_kind,
                "threshold": best_thr,
                "test_cash_change_baseline": baseline_test["cash_change"],
                "test_cash_change_ml": ml_test["cash_change"],
                "test_delta_ml_vs_baseline": ml_test["cash_change"] - baseline_test["cash_change"],
                "test_trades_baseline": baseline_test["trades_taken"],
                "test_trades_ml": ml_test["trades_taken"],
            }
        )

        i += args.step_days

    if not rows:
        raise RuntimeError("No folds generated.")

    fold_df = pd.DataFrame(rows)
    covered_test_days = int(len(all_test_dates))
    start_cash_total = float(args.cash_per_symbol * len(symbols))
    sum_baseline = float(fold_df["test_cash_change_baseline"].sum())
    sum_ml = float(fold_df["test_cash_change_ml"].sum())

    summary = pd.DataFrame(
        [
            {
                "mode": "baseline_global",
                "total_cash_change": sum_baseline,
                "annualized_cash_growth_pct": _annualized_growth(sum_baseline, start_cash_total, covered_test_days),
                "positive_folds": int((fold_df["test_cash_change_baseline"] > 0).sum()),
                "folds": int(len(fold_df)),
                "trades_total": int(fold_df["test_trades_baseline"].sum()),
            },
            {
                "mode": "ml_filtered" if args.selection_mode == "candidate_rank" else "ml_gate_baseline",
                "total_cash_change": sum_ml,
                "annualized_cash_growth_pct": _annualized_growth(sum_ml, start_cash_total, covered_test_days),
                "positive_folds": int((fold_df["test_cash_change_ml"] > 0).sum()),
                "folds": int(len(fold_df)),
                "trades_total": int(fold_df["test_trades_ml"].sum()),
            },
        ]
    )
    summary["meets_target"] = summary["annualized_cash_growth_pct"] >= float(args.target_growth_pct)

    print("=== Walk-Forward ML Filter Setup ===")
    print(
        f"symbols={len(symbols)} dropped={len(dropped)} data_source={args.data_source} "
        f"train_days={args.train_days} test_days={args.test_days} step_days={args.step_days} "
        f"htf_gate={args.htf_gate} selection_mode={args.selection_mode} min_history_days={args.min_history_days} "
        f"slippage_bps={args.slippage_bps} commission_per_share={args.commission_per_share}"
    )
    if dropped:
        print(f"dropped_symbols={','.join(dropped)}")
    print(f"window_start={common_dates[0]} window_end={common_dates[-1]}")
    print(f"covered_test_days={covered_test_days}")
    print(f"target_growth_pct={args.target_growth_pct:.2f}")
    print(f"model_kinds={model_kinds}")

    print("\n=== Fold Results ===")
    print(fold_df.to_string(index=False))

    print("\n=== Summary ===")
    print(summary.to_string(index=False))

    if args.save_fold_csv.strip():
        out = Path(args.save_fold_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        fold_df.to_csv(out, index=False)
        print(f"\nSaved folds CSV: {out}")
    if args.save_summary_csv.strip():
        out = Path(args.save_summary_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out, index=False)
        print(f"Saved summary CSV: {out}")
if __name__ == "__main__":
    main()
