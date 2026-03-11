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
    p = argparse.ArgumentParser(description="Meta-select best ORB preset per symbol (train/test).")
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
    p.add_argument("--train-ratio", type=float, default=0.6)
    p.add_argument("--start-shares", type=int, default=100)
    p.add_argument("--start-cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--trade-fraction", type=float, default=0.2)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    orb = _load_orb_module()
    symbols, tv_exchange_map = orb._resolve_symbols_and_exchange_map(args)

    bars_by_symbol, common_dates = orb._load_market_data(
        symbols=symbols,
        period=args.period,
        interval=args.interval,
        data_source=args.data_source,
        tv_n_bars=args.tv_n_bars,
        tv_username=args.tv_username,
        tv_password=args.tv_password,
        tv_exchange_map=tv_exchange_map,
        default_exchange=args.tv_default_exchange,
        alpaca_key=args.alpaca_key,
        alpaca_secret=args.alpaca_secret,
        alpaca_feed=args.alpaca_feed,
        alpaca_base_url=args.alpaca_base_url,
    )

    split_idx = max(1, int(len(common_dates) * args.train_ratio))
    split_idx = min(split_idx, len(common_dates) - 1)
    train_dates = common_dates[:split_idx]
    test_dates = common_dates[split_idx:]

    train_set = set(train_dates)
    test_set = set(test_dates)

    bars_train = _subset_bars(bars_by_symbol, train_set)
    bars_test = _subset_bars(bars_by_symbol, test_set)

    presets = [
        {"name": "baseline", "direction_mode": "both", "min_breakout_frac": 0.0, "min_rvol": 0.0, "use_vwap_filter": False, "use_ema_slope_filter": False, "entry_cutoff_hhmm": "", "exit_mode": "close"},
        {"name": "up_only", "direction_mode": "up", "min_breakout_frac": 0.0, "min_rvol": 0.0, "use_vwap_filter": False, "use_ema_slope_filter": False, "entry_cutoff_hhmm": "", "exit_mode": "close"},
        {"name": "down_only", "direction_mode": "down", "min_breakout_frac": 0.0, "min_rvol": 0.0, "use_vwap_filter": False, "use_ema_slope_filter": False, "entry_cutoff_hhmm": "", "exit_mode": "close"},
        {"name": "strength_0.15", "direction_mode": "both", "min_breakout_frac": 0.15, "min_rvol": 0.0, "use_vwap_filter": False, "use_ema_slope_filter": False, "entry_cutoff_hhmm": "", "exit_mode": "close"},
        {"name": "rvol_1.3", "direction_mode": "both", "min_breakout_frac": 0.0, "min_rvol": 1.3, "use_vwap_filter": False, "use_ema_slope_filter": False, "entry_cutoff_hhmm": "", "exit_mode": "close"},
    ]

    chosen: dict[str, dict] = {}
    selection_rows: list[dict] = []

    for sym in symbols:
        best_preset = None
        best_cash = -10**18
        for p in presets:
            _, sm, _ = orb.run_shared_cash_backtest(
                symbols=[sym],
                period=args.period,
                interval=args.interval,
                start_shares_each=args.start_shares,
                start_cash_total=args.start_cash_per_symbol,
                trade_fraction=args.trade_fraction,
                data_source=args.data_source,
                tv_n_bars=args.tv_n_bars,
                tv_username=args.tv_username,
                tv_password=args.tv_password,
                tv_exchange_map=tv_exchange_map,
                tv_default_exchange=args.tv_default_exchange,
                alpaca_key=args.alpaca_key,
                alpaca_secret=args.alpaca_secret,
                alpaca_feed=args.alpaca_feed,
                alpaca_base_url=args.alpaca_base_url,
                direction_mode=p["direction_mode"],
                min_breakout_frac=p["min_breakout_frac"],
                min_rvol=p["min_rvol"],
                use_vwap_filter=p["use_vwap_filter"],
                use_ema_slope_filter=p["use_ema_slope_filter"],
                entry_cutoff_hhmm=p["entry_cutoff_hhmm"],
                exit_mode=p["exit_mode"],
                bars_by_symbol={sym: bars_train[sym]},
                common_dates=train_dates,
            )
            if sm["cash_change"] > best_cash:
                best_cash = sm["cash_change"]
                best_preset = p
        chosen[sym] = best_preset
        selection_rows.append({"symbol": sym, "chosen_preset": best_preset["name"], "train_cash_change": best_cash})

    test_rows: list[dict] = []
    baseline_total_cash = 0.0
    blended_total_cash = 0.0

    for sym in symbols:
        # Baseline on test
        _, sm_base, _ = orb.run_shared_cash_backtest(
            symbols=[sym],
            period=args.period,
            interval=args.interval,
            start_shares_each=args.start_shares,
            start_cash_total=args.start_cash_per_symbol,
            trade_fraction=args.trade_fraction,
            data_source=args.data_source,
            tv_n_bars=args.tv_n_bars,
            tv_username=args.tv_username,
            tv_password=args.tv_password,
            tv_exchange_map=tv_exchange_map,
            tv_default_exchange=args.tv_default_exchange,
            alpaca_key=args.alpaca_key,
            alpaca_secret=args.alpaca_secret,
            alpaca_feed=args.alpaca_feed,
            alpaca_base_url=args.alpaca_base_url,
            direction_mode="both",
            min_breakout_frac=0.0,
            min_rvol=0.0,
            use_vwap_filter=False,
            use_ema_slope_filter=False,
            entry_cutoff_hhmm="",
            exit_mode="close",
            bars_by_symbol={sym: bars_test[sym]},
            common_dates=test_dates,
        )
        baseline_total_cash += sm_base["final_cash_total"]

        p = chosen[sym]
        _, sm_sel, _ = orb.run_shared_cash_backtest(
            symbols=[sym],
            period=args.period,
            interval=args.interval,
            start_shares_each=args.start_shares,
            start_cash_total=args.start_cash_per_symbol,
            trade_fraction=args.trade_fraction,
            data_source=args.data_source,
            tv_n_bars=args.tv_n_bars,
            tv_username=args.tv_username,
            tv_password=args.tv_password,
            tv_exchange_map=tv_exchange_map,
            tv_default_exchange=args.tv_default_exchange,
            alpaca_key=args.alpaca_key,
            alpaca_secret=args.alpaca_secret,
            alpaca_feed=args.alpaca_feed,
            alpaca_base_url=args.alpaca_base_url,
            direction_mode=p["direction_mode"],
            min_breakout_frac=p["min_breakout_frac"],
            min_rvol=p["min_rvol"],
            use_vwap_filter=p["use_vwap_filter"],
            use_ema_slope_filter=p["use_ema_slope_filter"],
            entry_cutoff_hhmm=p["entry_cutoff_hhmm"],
            exit_mode=p["exit_mode"],
            bars_by_symbol={sym: bars_test[sym]},
            common_dates=test_dates,
        )
        blended_total_cash += sm_sel["final_cash_total"]

        test_rows.append(
            {
                "symbol": sym,
                "test_baseline_cash_change": sm_base["cash_change"],
                "test_blended_cash_change": sm_sel["cash_change"],
                "chosen_preset": p["name"],
            }
        )

    print("=== ORB Meta Selection ===")
    print(pd.DataFrame(selection_rows).to_string(index=False))
    print("\n=== Test Results (Independent Cash Per Symbol) ===")
    print(pd.DataFrame(test_rows).to_string(index=False))
    start_total_cash = args.start_cash_per_symbol * len(symbols)
    print("\nTotals:")
    print(f"train_start={train_dates[0]} train_end={train_dates[-1]} | test_start={test_dates[0]} test_end={test_dates[-1]}")
    print(f"baseline_final_cash={baseline_total_cash:,.2f} baseline_cash_change={baseline_total_cash - start_total_cash:,.2f}")
    print(f"blended_final_cash={blended_total_cash:,.2f} blended_cash_change={blended_total_cash - start_total_cash:,.2f}")
    print(f"delta_vs_baseline={(blended_total_cash - baseline_total_cash):,.2f}")


if __name__ == "__main__":
    main()
