from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pandas as pd

DEFAULT_ENGINE_PATH = Path(__file__).resolve().parent.parent / "ORB_TEST" / "backtest_orb_shared_cash.py"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "data" / "market_data_cache.sqlite"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "data" / "trade_dataset.csv"


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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build per-trade dataset for ML filtering from ORB runs.")
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

    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--trade-fraction", type=float, default=0.2)

    p.add_argument("--direction-mode", choices=["both", "up", "down"], default="both")
    p.add_argument("--min-breakout-frac", type=float, default=0.0)
    p.add_argument("--min-rvol", type=float, default=0.0)
    p.add_argument("--rvol-lookback-bars", type=int, default=6)
    p.add_argument("--use-vwap-filter", action="store_true")
    p.add_argument("--use-ema-slope-filter", action="store_true")
    p.add_argument("--entry-cutoff", default="")

    p.add_argument("--exit-mode", choices=["close", "bracket"], default="close")
    p.add_argument("--stop-or-mult", type=float, default=0.6)
    p.add_argument("--target-or-mult", type=float, default=1.2)
    p.add_argument("--time-stop-bars", type=int, default=0)
    p.add_argument("--min-progress-r", type=float, default=0.3)
    p.add_argument("--break-even-r", type=float, default=0.0)
    p.add_argument("--trail-mode", choices=["none", "ema", "vwap"], default="none")
    p.add_argument("--trail-after-r", type=float, default=1.0)

    p.add_argument("--output-csv", default=str(DEFAULT_OUTPUT))
    return p.parse_args()


def _featureize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out[out["quantity"] > 0].reset_index(drop=True)
    if out.empty:
        return out

    out["entry_time"] = pd.to_datetime(out["entry_time"], errors="coerce")
    out["exit_time"] = pd.to_datetime(out["exit_time"], errors="coerce")
    out["session_date"] = pd.to_datetime(out["session_date"], errors="coerce").dt.date.astype(str)

    out["direction_up"] = (out["direction"] == "UP_TRUE").astype(int)
    out["or_width_pct"] = out["or_width"] / out["entry_price"].replace(0, pd.NA)
    out["confirm_rvol"] = pd.to_numeric(out["confirm_rvol"], errors="coerce").fillna(0.0)
    out["entry_minute_of_day"] = out["entry_time"].dt.hour * 60 + out["entry_time"].dt.minute
    out["entry_minutes_from_open"] = out["entry_minute_of_day"] - (9 * 60 + 30)
    out["label_win"] = (out["pnl"] > 0).astype(int)

    keep = [
        "session_date",
        "symbol",
        "direction_up",
        "entry_price",
        "or_width",
        "or_width_pct",
        "confirm_rvol",
        "entry_minute_of_day",
        "entry_minutes_from_open",
        "exit_reason",
        "pnl",
        "label_win",
    ]
    return out[keep].copy()


def main() -> None:
    args = _parse_args()
    orb = _load_orb_module(args.engine_path)

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
        cache_db=args.cache_db,
        cache_disabled=args.no_cache,
        cache_refresh=args.cache_refresh,
    )

    all_trades: list[pd.DataFrame] = []
    for sym in symbols:
        trades_df, _, _ = orb.run_shared_cash_backtest(
            symbols=[sym],
            period=args.period,
            interval=args.interval,
            start_shares_each=args.start_shares_each,
            start_cash_total=args.cash_per_symbol,
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
            cache_db=args.cache_db,
            cache_disabled=args.no_cache,
            cache_refresh=False,
            direction_mode=args.direction_mode,
            min_breakout_frac=args.min_breakout_frac,
            min_rvol=args.min_rvol,
            rvol_lookback_bars=args.rvol_lookback_bars,
            use_vwap_filter=args.use_vwap_filter,
            use_ema_slope_filter=args.use_ema_slope_filter,
            entry_cutoff_hhmm=args.entry_cutoff,
            exit_mode=args.exit_mode,
            stop_or_mult=args.stop_or_mult,
            target_or_mult=args.target_or_mult,
            time_stop_bars=args.time_stop_bars,
            min_progress_r=args.min_progress_r,
            break_even_r=args.break_even_r,
            trail_mode=args.trail_mode,
            trail_after_r=args.trail_after_r,
            bars_by_symbol={sym: bars_by_symbol[sym]},
            common_dates=common_dates,
        )
        all_trades.append(trades_df)

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    feat = _featureize(trades)

    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    feat.to_csv(out, index=False)

    print("=== Trade Dataset Built ===")
    print(f"rows={len(feat)} symbols={len(symbols)}")
    if not feat.empty:
        win_rate = float(feat["label_win"].mean())
        pnl_sum = float(feat["pnl"].sum())
        print(f"win_rate={win_rate:.4f} pnl_sum={pnl_sum:.2f}")
        print(f"start_date={feat['session_date'].min()} end_date={feat['session_date'].max()}")
    print(f"output={out}")


if __name__ == "__main__":
    main()
