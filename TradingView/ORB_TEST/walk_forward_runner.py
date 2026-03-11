from __future__ import annotations

import argparse
import importlib.util
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
    p = argparse.ArgumentParser(description="Walk-forward evaluation for ORB strategy variants.")
    p.add_argument("--symbols", default="QQQ,NVDA,SPY")
    p.add_argument("--symbols-file", default="")
    p.add_argument("--data-source", choices=["yfinance", "tvdatafeed", "alpaca"], default="tvdatafeed")
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

    p.add_argument("--pool-mode", choices=["shared", "independent"], default="independent")
    p.add_argument("--shared-cash-total", type=float, default=30000.0)
    p.add_argument("--cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--trade-fraction", type=float, default=0.2)

    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--min-symbols", type=int, default=3)
    return p.parse_args()


def _aggregate_mode(
    orb,
    *,
    symbols: list[str],
    mode: str,
    shared_cash_total: float,
    cash_per_symbol: float,
    bars_by_symbol: dict[str, pd.DataFrame],
    common_dates: list[str],
    common_kwargs: dict,
    preset: dict,
) -> dict:
    kwargs = dict(common_kwargs)
    kwargs.update(
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
    )

    if mode == "shared":
        _, sm, _ = orb.run_shared_cash_backtest(
            symbols=symbols,
            start_cash_total=shared_cash_total,
            bars_by_symbol=bars_by_symbol,
            common_dates=common_dates,
            **kwargs,
        )
        return {
            "cash_change": float(sm["cash_change"]),
            "final_cash_total": float(sm["final_cash_total"]),
            "alpha_vs_bh": float(sm["strategy_alpha_vs_buy_hold"]),
            "trades_executed": int(sm["trades_executed"]),
        }

    if mode == "independent":
        final_cash_total = 0.0
        alpha_total = 0.0
        trades_total = 0
        for sym in symbols:
            _, sm, _ = orb.run_shared_cash_backtest(
                symbols=[sym],
                start_cash_total=cash_per_symbol,
                bars_by_symbol={sym: bars_by_symbol[sym]},
                common_dates=common_dates,
                **kwargs,
            )
            final_cash_total += float(sm["final_cash_total"])
            alpha_total += float(sm["strategy_alpha_vs_buy_hold"])
            trades_total += int(sm["trades_executed"])
        start_total = cash_per_symbol * len(symbols)
        return {
            "cash_change": float(final_cash_total - start_total),
            "final_cash_total": float(final_cash_total),
            "alpha_vs_bh": float(alpha_total),
            "trades_executed": int(trades_total),
        }

    raise ValueError(f"Unsupported pool mode: {mode}")


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


def main() -> None:
    args = _parse_args()
    orb = _load_orb_module()

    symbols, bars_by_symbol, common_dates, dropped = _load_symbols_data_robust(orb, args)

    presets = [
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
            "name": "bracket_08_15_ts3",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.8,
            "target_or_mult": 1.5,
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
        {
            "name": "bracket_08_12_vwaptrail",
            "direction_mode": "both",
            "min_breakout_frac": 0.0,
            "min_rvol": 0.0,
            "use_vwap_filter": False,
            "use_ema_slope_filter": False,
            "entry_cutoff_hhmm": "",
            "exit_mode": "bracket",
            "stop_or_mult": 0.8,
            "target_or_mult": 1.2,
            "time_stop_bars": 0,
            "min_progress_r": 0.3,
            "break_even_r": 0.7,
            "trail_mode": "vwap",
            "trail_after_r": 1.0,
        },
    ]

    common_kwargs = dict(
        period=args.period,
        interval=args.interval,
        start_shares_each=args.start_shares_each,
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
    )

    train_days = args.train_days
    test_days = args.test_days
    step_days = args.step_days

    rows: list[dict] = []
    i = 0
    while i + train_days + test_days <= len(common_dates):
        train_dates = common_dates[i : i + train_days]
        test_dates = common_dates[i + train_days : i + train_days + test_days]
        train_set = set(train_dates)
        test_set = set(test_dates)
        bars_train = _subset_bars(bars_by_symbol, train_set)
        bars_test = _subset_bars(bars_by_symbol, test_set)

        best_name = ""
        best_train_cash = -10**18
        for p in presets:
            train_res = _aggregate_mode(
                orb,
                symbols=symbols,
                mode=args.pool_mode,
                shared_cash_total=args.shared_cash_total,
                cash_per_symbol=args.cash_per_symbol,
                bars_by_symbol=bars_train,
                common_dates=train_dates,
                common_kwargs=common_kwargs,
                preset=p,
            )
            if train_res["cash_change"] > best_train_cash:
                best_train_cash = train_res["cash_change"]
                best_name = p["name"]

        chosen = next(x for x in presets if x["name"] == best_name)
        test_chosen = _aggregate_mode(
            orb,
            symbols=symbols,
            mode=args.pool_mode,
            shared_cash_total=args.shared_cash_total,
            cash_per_symbol=args.cash_per_symbol,
            bars_by_symbol=bars_test,
            common_dates=test_dates,
            common_kwargs=common_kwargs,
            preset=chosen,
        )
        baseline = _aggregate_mode(
            orb,
            symbols=symbols,
            mode=args.pool_mode,
            shared_cash_total=args.shared_cash_total,
            cash_per_symbol=args.cash_per_symbol,
            bars_by_symbol=bars_test,
            common_dates=test_dates,
            common_kwargs=common_kwargs,
            preset=presets[0],
        )
        rows.append(
            {
                "fold": len(rows) + 1,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "selected_preset": chosen["name"],
                "train_cash_change": best_train_cash,
                "test_cash_change_selected": test_chosen["cash_change"],
                "test_cash_change_baseline": baseline["cash_change"],
                "test_delta_vs_baseline": test_chosen["cash_change"] - baseline["cash_change"],
            }
        )

        i += step_days

    if not rows:
        raise RuntimeError("No folds generated. Reduce train-days/test-days or increase period window.")

    fold_df = pd.DataFrame(rows)
    sum_selected = float(fold_df["test_cash_change_selected"].sum())
    sum_baseline = float(fold_df["test_cash_change_baseline"].sum())
    wins_selected = int((fold_df["test_cash_change_selected"] > 0).sum())
    wins_baseline = int((fold_df["test_cash_change_baseline"] > 0).sum())
    folds = int(len(fold_df))

    print("=== Walk-Forward Setup ===")
    print(
        f"pool_mode={args.pool_mode} symbols={len(symbols)} dropped={len(dropped)} "
        f"train_days={train_days} test_days={test_days} step_days={step_days}"
    )
    if dropped:
        print(f"dropped_symbols={','.join(dropped)}")
    print(f"window_start={common_dates[0]} window_end={common_dates[-1]}")

    print("\n=== Fold Results ===")
    print(fold_df.to_string(index=False))

    print("\n=== Walk-Forward Summary (Test Folds) ===")
    print(f"folds={folds}")
    print(f"selected_total_cash_change={sum_selected:,.2f}")
    print(f"baseline_total_cash_change={sum_baseline:,.2f}")
    print(f"selected_minus_baseline={sum_selected - sum_baseline:,.2f}")
    print(f"selected_positive_folds={wins_selected}/{folds}")
    print(f"baseline_positive_folds={wins_baseline}/{folds}")


if __name__ == "__main__":
    main()
