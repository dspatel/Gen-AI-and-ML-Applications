from __future__ import annotations

import argparse
import importlib.util
from collections import Counter
from pathlib import Path

import pandas as pd


def _load_orb_module():
    p = Path(__file__).with_name("backtest_orb_shared_cash.py")
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
    p.add_argument("--cache-db", default="", help="Optional cache path. Empty uses module default.")
    p.add_argument("--cache-refresh", action="store_true")
    p.add_argument("--no-cache", action="store_true")

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
    args: argparse.Namespace,
    preset: dict,
) -> float:
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
        common_dates=dates,
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


def _mode_summary(fold_df: pd.DataFrame, mode_col: str, baseline_col: str) -> dict:
    vals = fold_df[mode_col].astype(float)
    return {
        "mode": mode_col.replace("test_cash_change_", ""),
        "total_cash_change": float(vals.sum()),
        "median_fold_cash_change": float(vals.median()),
        "positive_folds": int((vals > 0).sum()),
        "folds": int(len(vals)),
        "positive_rate": float((vals > 0).mean()),
        "max_drawdown": float(_max_drawdown_from_changes(vals.tolist())),
        "delta_vs_global_total": float(vals.sum() - fold_df[baseline_col].astype(float).sum()),
    }


def main() -> None:
    args = _parse_args()
    orb = _load_orb_module()
    if not args.cache_db:
        args.cache_db = orb.CACHE_DB_DEFAULT

    symbols, bars_by_symbol, common_dates, dropped = _load_symbols_data_robust(orb, args)
    presets = _preset_pool()
    preset_by_name = {p["name"]: p for p in presets}
    preset_names = [p["name"] for p in presets]

    train_days = args.train_days
    test_days = args.test_days
    step_days = args.step_days

    rows: list[dict] = []
    global_pick_counts: Counter[str] = Counter()
    symbol_pick_counts: Counter[str] = Counter()
    hybrid_pick_counts: Counter[str] = Counter()

    i = 0
    while i + train_days + test_days <= len(common_dates):
        train_dates = common_dates[i : i + train_days]
        test_dates = common_dates[i + train_days : i + train_days + test_days]
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
                    args=args,
                    preset=p,
                )
                test_scores[sym][pname] = _eval_symbol_preset(
                    orb,
                    symbol=sym,
                    bars=bars_test[sym],
                    dates=test_dates,
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
    summary_df = pd.DataFrame(
        [
            _mode_summary(fold_df, "test_cash_change_global", "test_cash_change_global"),
            _mode_summary(fold_df, "test_cash_change_symbol_specific", "test_cash_change_global"),
            _mode_summary(fold_df, "test_cash_change_hybrid", "test_cash_change_global"),
            _mode_summary(fold_df, "test_cash_change_baseline_fixed", "test_cash_change_global"),
        ]
    )

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
    print(f"hybrid_min_train_edge={args.hybrid_min_train_edge:.2f}")

    print("\n=== Fold Results ===")
    print(fold_df.to_string(index=False))

    print("\n=== Mode Summary (Test Folds) ===")
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


if __name__ == "__main__":
    main()
