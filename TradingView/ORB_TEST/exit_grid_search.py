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


def _parse_float_list(s: str) -> list[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def _parse_int_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _parse_str_list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Grid search exit settings for ORB shared-cash backtest.")
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

    p.add_argument("--pool-mode", choices=["shared", "independent"], default="shared")
    p.add_argument("--shared-cash-total", type=float, default=30000.0)
    p.add_argument("--cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--trade-fraction", type=float, default=0.2)

    p.add_argument("--stop-values", default="0.6,0.8")
    p.add_argument("--target-values", default="1.0,1.2,1.5")
    p.add_argument("--time-stop-values", default="0,3")
    p.add_argument("--break-even-values", default="0.0,0.7")
    p.add_argument("--trail-modes", default="none,ema,vwap")
    p.add_argument("--trail-after-r", type=float, default=1.0)
    p.add_argument("--min-progress-r", type=float, default=0.3)
    p.add_argument("--top-n", type=int, default=10)
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
    exit_kwargs: dict,
) -> dict:
    if mode == "shared":
        _, sm, _ = orb.run_shared_cash_backtest(
            symbols=symbols,
            start_cash_total=shared_cash_total,
            bars_by_symbol=bars_by_symbol,
            common_dates=common_dates,
            **common_kwargs,
            **exit_kwargs,
        )
        return {
            "final_cash_total": float(sm["final_cash_total"]),
            "cash_change": float(sm["cash_change"]),
            "final_equity_total": float(sm["final_equity_total"]),
            "net_pnl_total": float(sm["net_pnl_total"]),
            "strategy_alpha_vs_buy_hold": float(sm["strategy_alpha_vs_buy_hold"]),
            "trades_executed": int(sm["trades_executed"]),
            "days_with_any_signal": int(sm["days_with_any_signal"]),
            "start_date": sm["start_date"],
            "end_date": sm["end_date"],
        }

    if mode == "independent":
        cash_total = 0.0
        equity_total = 0.0
        pnl_total = 0.0
        alpha_total = 0.0
        trades = 0
        signal_days = 0
        start_date = None
        end_date = None
        for sym in symbols:
            _, sm, _ = orb.run_shared_cash_backtest(
                symbols=[sym],
                start_cash_total=cash_per_symbol,
                bars_by_symbol={sym: bars_by_symbol[sym]},
                common_dates=common_dates,
                **common_kwargs,
                **exit_kwargs,
            )
            cash_total += float(sm["final_cash_total"])
            equity_total += float(sm["final_equity_total"])
            pnl_total += float(sm["net_pnl_total"])
            alpha_total += float(sm["strategy_alpha_vs_buy_hold"])
            trades += int(sm["trades_executed"])
            signal_days += int(sm["days_with_any_signal"])
            start_date = sm["start_date"] if start_date is None else min(start_date, sm["start_date"])
            end_date = sm["end_date"] if end_date is None else max(end_date, sm["end_date"])
        start_cash_total = cash_per_symbol * len(symbols)
        return {
            "final_cash_total": cash_total,
            "cash_change": cash_total - start_cash_total,
            "final_equity_total": equity_total,
            "net_pnl_total": pnl_total,
            "strategy_alpha_vs_buy_hold": alpha_total,
            "trades_executed": trades,
            "days_with_any_signal": signal_days,
            "start_date": start_date,
            "end_date": end_date,
        }

    raise ValueError(f"Unsupported pool mode: {mode}")


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

    common_kwargs = dict(
        period=args.period,
        interval=args.interval,
        start_shares_each=args.start_shares_each,
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
    )

    baseline = _aggregate_mode(
        orb,
        symbols=symbols,
        mode=args.pool_mode,
        shared_cash_total=args.shared_cash_total,
        cash_per_symbol=args.cash_per_symbol,
        bars_by_symbol=bars_by_symbol,
        common_dates=common_dates,
        common_kwargs=common_kwargs,
        exit_kwargs=dict(
            exit_mode="close",
            stop_or_mult=0.6,
            target_or_mult=1.2,
            time_stop_bars=0,
            min_progress_r=args.min_progress_r,
            break_even_r=0.0,
            trail_mode="none",
            trail_after_r=args.trail_after_r,
        ),
    )

    stop_vals = _parse_float_list(args.stop_values)
    target_vals = _parse_float_list(args.target_values)
    time_vals = _parse_int_list(args.time_stop_values)
    be_vals = _parse_float_list(args.break_even_values)
    trail_modes = _parse_str_list(args.trail_modes)

    rows: list[dict] = []
    for stop in stop_vals:
        for target in target_vals:
            for time_stop in time_vals:
                for be in be_vals:
                    for trail in trail_modes:
                        m = _aggregate_mode(
                            orb,
                            symbols=symbols,
                            mode=args.pool_mode,
                            shared_cash_total=args.shared_cash_total,
                            cash_per_symbol=args.cash_per_symbol,
                            bars_by_symbol=bars_by_symbol,
                            common_dates=common_dates,
                            common_kwargs=common_kwargs,
                            exit_kwargs=dict(
                                exit_mode="bracket",
                                stop_or_mult=stop,
                                target_or_mult=target,
                                time_stop_bars=time_stop,
                                min_progress_r=args.min_progress_r,
                                break_even_r=be,
                                trail_mode=trail,
                                trail_after_r=args.trail_after_r,
                            ),
                        )
                        rows.append(
                            {
                                "stop": stop,
                                "target": target,
                                "time_stop_bars": time_stop,
                                "break_even_r": be,
                                "trail_mode": trail,
                                "cash_change": m["cash_change"],
                                "final_cash_total": m["final_cash_total"],
                                "alpha_vs_bh": m["strategy_alpha_vs_buy_hold"],
                                "trades_executed": m["trades_executed"],
                            }
                        )

    out = pd.DataFrame(rows).sort_values("cash_change", ascending=False).reset_index(drop=True)

    print("=== Exit Grid Baseline (EOD Close) ===")
    print(
        f"pool_mode={args.pool_mode} cash_change={baseline['cash_change']:.2f} "
        f"final_cash_total={baseline['final_cash_total']:.2f} "
        f"alpha_vs_bh={baseline['strategy_alpha_vs_buy_hold']:.2f} "
        f"start_date={baseline['start_date']} end_date={baseline['end_date']}"
    )
    print("\n=== Exit Grid Top Results ===")
    print(out.head(args.top_n).to_string(index=False))


if __name__ == "__main__":
    main()
