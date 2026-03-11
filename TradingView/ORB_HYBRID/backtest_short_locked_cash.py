from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import exit_optimizer_walkforward as ex

DEFAULT_SYMBOLS_FILE = Path(__file__).resolve().parent / "universes" / "focus_symbols_v1.txt"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "alpaca_daily_cache.sqlite"
DEFAULT_TRADES_CSV = Path(__file__).resolve().parent / "reports" / "short_locked_cash_trades.csv"


@dataclass
class OpenPos:
    trade_id: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    symbol: str
    ret: float
    notional: float


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Cash backtest for the locked short exit strategy across selected symbols. "
            "Uses daily short entries and simulates capital allocation with reserved notional."
        )
    )
    p.add_argument("--symbols", default="")
    p.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS_FILE))
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex")
    p.add_argument("--adjustment", choices=["raw", "split", "dividend", "all"], default="split")
    p.add_argument("--breakout-window", type=int, default=21)
    p.add_argument("--range-mode", choices=["rolling", "anchored"], default="anchored")
    p.add_argument("--setup-max-days", type=int, default=15)
    p.add_argument("--eval-segment", choices=["all", "test"], default="test")
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--starting-cash", type=float, default=160000.0)
    p.add_argument("--trade-fraction", type=float, default=0.2)
    p.add_argument("--min-trade-notional", type=float, default=100.0)
    p.add_argument("--save-trades-csv", default=str(DEFAULT_TRADES_CSV))
    return p.parse_args()


def _locked_short_cfg() -> dict[str, Any]:
    return {
        "exit_type": "hybrid",
        "atr_mult": 1.5,
        "hard_stop_atr": 1.0,
        "breakeven_r": 0.0,
        "use_ema_flip": False,
        "max_hold": 15,
    }


def _build_trade_list(
    daily: pd.DataFrame,
    breakout_window: int,
    range_mode: str,
    setup_max_days: int,
    eval_segment: str,
    train_ratio: float,
) -> pd.DataFrame:
    entries = ex._build_entries(
        daily=daily,
        breakout_window=int(breakout_window),
        range_mode=str(range_mode),
        setup_max_days=int(setup_max_days),
        side_mode="short",
    )
    if entries.empty:
        return pd.DataFrame()

    if eval_segment == "test":
        dts = sorted(pd.to_datetime(daily["date"]).dt.normalize().unique().tolist())
        split_idx = int(len(dts) * float(train_ratio))
        split_idx = max(40, min(split_idx, len(dts) - 20))
        test_start = pd.Timestamp(dts[split_idx]).normalize()
        entries = entries[entries["date"].dt.normalize() >= test_start].copy()
        if entries.empty:
            return pd.DataFrame()

    frames = ex._build_symbol_frames(daily)
    cfg = _locked_short_cfg()
    rows: list[dict[str, Any]] = []
    for t in entries.itertuples(index=False):
        sym = str(t.symbol)
        frame = frames.get(sym)
        if frame is None or int(t.idx) >= len(frame) - 1:
            continue
        ret, exit_date, reason = ex._eval_trade(frame, pd.Series(t._asdict()), cfg)
        rows.append(
            {
                "symbol": sym,
                "entry_date": pd.Timestamp(t.date).normalize(),
                "exit_date": pd.Timestamp(exit_date).normalize(),
                "entry_idx": int(t.idx),
                "entry_price": float(t.entry_price),
                "ret": float(ret),
                "exit_reason": str(reason),
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values(["entry_date", "symbol"]).reset_index(drop=True)
    out["trade_id"] = np.arange(len(out), dtype=int)
    return out


def _simulate_cash(
    trades: pd.DataFrame,
    starting_cash: float,
    trade_fraction: float,
    min_trade_notional: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame()

    cash = float(starting_cash)
    trade_fraction = float(trade_fraction)
    min_trade_notional = float(min_trade_notional)

    open_by_date: dict[pd.Timestamp, list[OpenPos]] = {}
    close_by_date: dict[pd.Timestamp, list[OpenPos]] = {}
    for r in trades.itertuples(index=False):
        op = OpenPos(
            trade_id=int(r.trade_id),
            entry_date=pd.Timestamp(r.entry_date),
            exit_date=pd.Timestamp(r.exit_date),
            symbol=str(r.symbol),
            ret=float(r.ret),
            notional=0.0,
        )
        open_by_date.setdefault(op.entry_date, []).append(op)
        close_by_date.setdefault(op.exit_date, []).append(op)

    dates = sorted(set(open_by_date.keys()) | set(close_by_date.keys()))
    active: dict[int, OpenPos] = {}
    trade_rows: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []

    for dt in dates:
        # Close first to free reserved cash before new entries.
        for op in close_by_date.get(dt, []):
            live = active.pop(op.trade_id, None)
            if live is None:
                continue
            pnl = live.notional * float(live.ret)
            cash_before = cash
            cash += live.notional + pnl
            trade_rows.append(
                {
                    "trade_id": int(live.trade_id),
                    "symbol": live.symbol,
                    "entry_date": live.entry_date,
                    "exit_date": live.exit_date,
                    "ret": float(live.ret),
                    "notional": float(live.notional),
                    "pnl": float(pnl),
                    "cash_before_close": float(cash_before),
                    "cash_after_close": float(cash),
                    "status": "closed",
                }
            )

        # Open new entries.
        for op in open_by_date.get(dt, []):
            alloc = cash * trade_fraction
            if alloc < min_trade_notional:
                trade_rows.append(
                    {
                        "trade_id": int(op.trade_id),
                        "symbol": op.symbol,
                        "entry_date": op.entry_date,
                        "exit_date": op.exit_date,
                        "ret": float(op.ret),
                        "notional": float(0.0),
                        "pnl": float(0.0),
                        "cash_before_close": float(cash),
                        "cash_after_close": float(cash),
                        "status": "skipped_min_notional",
                    }
                )
                continue
            cash -= alloc
            live = OpenPos(
                trade_id=op.trade_id,
                entry_date=op.entry_date,
                exit_date=op.exit_date,
                symbol=op.symbol,
                ret=op.ret,
                notional=float(alloc),
            )
            active[live.trade_id] = live

        reserved = float(np.sum([x.notional for x in active.values()])) if active else 0.0
        equity_rows.append(
            {
                "date": dt,
                "cash_available": float(cash),
                "notional_reserved": reserved,
                "equity": float(cash + reserved),
                "open_positions": int(len(active)),
            }
        )

    # Force-close any leftovers at their modeled return (should be none with normalized dates)
    for live in list(active.values()):
        pnl = live.notional * float(live.ret)
        cash += live.notional + pnl
        trade_rows.append(
            {
                "trade_id": int(live.trade_id),
                "symbol": live.symbol,
                "entry_date": live.entry_date,
                "exit_date": live.exit_date,
                "ret": float(live.ret),
                "notional": float(live.notional),
                "pnl": float(pnl),
                "cash_before_close": np.nan,
                "cash_after_close": float(cash),
                "status": "closed_force",
            }
        )
    if active:
        equity_rows.append(
            {
                "date": max(dates) if dates else pd.Timestamp.utcnow().normalize(),
                "cash_available": float(cash),
                "notional_reserved": 0.0,
                "equity": float(cash),
                "open_positions": 0,
            }
        )

    trades_out = pd.DataFrame(trade_rows).sort_values(["entry_date", "trade_id"]).reset_index(drop=True)
    equity_out = pd.DataFrame(equity_rows).sort_values("date").reset_index(drop=True)
    return trades_out, equity_out


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    v = equity.astype(float).to_numpy()
    peak = np.maximum.accumulate(v)
    dd = np.where(peak > 0, (peak - v) / peak, 0.0)
    return float(np.nanmax(dd)) if len(dd) else 0.0


def main() -> None:
    args = _parse_args()
    symbols = ex._parse_symbols(args)
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Day"

    conn = sqlite3.connect(str(Path(args.db_path)))
    daily = ex._read_daily(conn, source_key=source_key, symbols=symbols)
    conn.close()
    if daily.empty:
        raise RuntimeError(f"No cached daily bars for source_key={source_key} and symbols={len(symbols)}.")

    trades = _build_trade_list(
        daily=daily,
        breakout_window=int(args.breakout_window),
        range_mode=str(args.range_mode),
        setup_max_days=int(args.setup_max_days),
        eval_segment=str(args.eval_segment),
        train_ratio=float(args.train_ratio),
    )
    if trades.empty:
        raise RuntimeError("No short trades generated for selected settings.")

    trades_sim, equity = _simulate_cash(
        trades=trades,
        starting_cash=float(args.starting_cash),
        trade_fraction=float(args.trade_fraction),
        min_trade_notional=float(args.min_trade_notional),
    )
    if trades_sim.empty or equity.empty:
        raise RuntimeError("Simulation produced no executable trades.")

    closed = trades_sim[trades_sim["status"].str.startswith("closed")].copy()
    skipped = trades_sim[trades_sim["status"] == "skipped_min_notional"].copy()
    start_cash = float(args.starting_cash)
    end_cash = float(equity["equity"].iloc[-1])
    growth_pct = 100.0 * (end_cash / start_cash - 1.0)
    win_rate = 100.0 * float(np.mean(closed["pnl"] > 0.0)) if not closed.empty else 0.0
    max_dd_pct = 100.0 * _max_drawdown(equity["equity"])
    avg_pnl = float(closed["pnl"].mean()) if not closed.empty else 0.0
    total_pnl = float(closed["pnl"].sum()) if not closed.empty else 0.0

    print("=== Short Locked Cash Backtest ===")
    print(
        f"symbols={len(symbols)} eval_segment={args.eval_segment} "
        f"date_range={daily['date'].min().date()} to {daily['date'].max().date()}"
    )
    print(
        f"entries={len(trades)} executed={len(closed)} skipped={len(skipped)} "
        f"trade_fraction={args.trade_fraction} min_notional={args.min_trade_notional:.2f}"
    )
    print(f"starting_cash={start_cash:,.2f}")
    print(f"ending_equity={end_cash:,.2f}")
    print(f"cash_growth_pct={growth_pct:.2f}")
    print(f"total_pnl={total_pnl:,.2f}")
    print(f"avg_pnl_per_trade={avg_pnl:,.2f}")
    print(f"win_rate_pct={win_rate:.2f}")
    print(f"max_drawdown_pct={max_dd_pct:.2f}")

    trades_path = Path(args.save_trades_csv)
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    trades_sim.to_csv(trades_path, index=False)
    eq_path = trades_path.with_name(trades_path.stem + "_equity.csv")
    equity.to_csv(eq_path, index=False)
    print(f"saved_trades_csv={trades_path}")
    print(f"saved_equity_csv={eq_path}")


if __name__ == "__main__":
    main()
