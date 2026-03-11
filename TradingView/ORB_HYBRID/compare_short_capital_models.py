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
DEFAULT_SUMMARY_CSV = Path(__file__).resolve().parent / "reports" / "short_capital_model_comparison.csv"


@dataclass
class LivePos:
    trade_id: int
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret: float
    notional: float
    score: float


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Compare short strategy capital models: independent 10k/symbol vs shared 100k allocator "
            "with reserve and ranked admission."
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

    p.add_argument("--independent-cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--independent-trade-fraction", type=float, default=0.2)

    p.add_argument("--shared-starting-cash", type=float, default=100000.0)
    p.add_argument("--shared-trade-fraction", type=float, default=0.2)
    p.add_argument("--shared-reserve-pct", type=float, default=0.35)
    p.add_argument("--shared-max-per-trade-pct", type=float, default=0.12)
    p.add_argument("--shared-max-per-symbol-pct", type=float, default=0.20)
    p.add_argument("--shared-max-open-positions", type=int, default=8)

    p.add_argument("--min-trade-notional", type=float, default=100.0)
    p.add_argument("--save-summary-csv", default=str(DEFAULT_SUMMARY_CSV))
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


def _build_candidates(
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
        idx = int(t.idx)
        frame = frames.get(sym)
        if frame is None or idx >= len(frame) - 1:
            continue
        ret, exit_date, reason = ex._eval_trade(frame, pd.Series(t._asdict()), cfg)
        entry_price = float(t.entry_price)
        breakout_level = float(t.breakout_level)
        atr = frame.at[idx, "atr14"]
        atr = float(atr) if not pd.isna(atr) else np.nan
        if pd.isna(atr) or atr <= 0:
            atr = 0.01 * entry_price

        # Signal strength proxy from available info at entry time.
        signal_strength = max((breakout_level - entry_price) / max(entry_price, 1e-9), 0.0)
        initial_stop = max(breakout_level, entry_price + 1.0 * atr)
        risk_pct = max((initial_stop - entry_price) / max(entry_price, 1e-9), 1e-6)
        score = signal_strength / risk_pct

        rows.append(
            {
                "symbol": sym,
                "entry_date": pd.Timestamp(t.date).normalize(),
                "exit_date": pd.Timestamp(exit_date).normalize(),
                "entry_idx": idx,
                "entry_price": entry_price,
                "breakout_level": breakout_level,
                "entry_atr14": atr,
                "ret": float(ret),
                "exit_reason": str(reason),
                "score": float(score),
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows).sort_values(["entry_date", "score", "symbol"], ascending=[True, False, True]).reset_index(
        drop=True
    )
    out["trade_id"] = np.arange(len(out), dtype=int)
    return out


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    v = equity.astype(float).to_numpy()
    p = np.maximum.accumulate(v)
    dd = np.where(p > 0, (p - v) / p, 0.0)
    return 100.0 * float(np.nanmax(dd)) if len(dd) else 0.0


def _cagr_pct(dates: pd.Series, equity: pd.Series) -> float:
    if dates.empty or equity.empty:
        return 0.0
    start = pd.Timestamp(dates.iloc[0])
    end = pd.Timestamp(dates.iloc[-1])
    days = max((end - start).days, 1)
    yrs = days / 365.25
    if yrs <= 0:
        return 0.0
    return 100.0 * ((float(equity.iloc[-1]) / max(float(equity.iloc[0]), 1e-9)) ** (1.0 / yrs) - 1.0)


def _simulate_independent(
    candidates: pd.DataFrame,
    symbols: list[str],
    cash_per_symbol: float,
    trade_fraction: float,
    min_notional: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    pools = {s: float(cash_per_symbol) for s in symbols}
    open_by_date: dict[pd.Timestamp, list[LivePos]] = {}
    close_by_date: dict[pd.Timestamp, list[LivePos]] = {}

    for r in candidates.itertuples(index=False):
        op = LivePos(
            trade_id=int(r.trade_id),
            symbol=str(r.symbol),
            entry_date=pd.Timestamp(r.entry_date),
            exit_date=pd.Timestamp(r.exit_date),
            ret=float(r.ret),
            notional=0.0,
            score=float(r.score),
        )
        open_by_date.setdefault(op.entry_date, []).append(op)
        close_by_date.setdefault(op.exit_date, []).append(op)

    dates = sorted(set(open_by_date.keys()) | set(close_by_date.keys()))
    active: dict[int, LivePos] = {}
    trade_rows: list[dict[str, Any]] = []
    eq_rows: list[dict[str, Any]] = []

    for dt in dates:
        for op in close_by_date.get(dt, []):
            live = active.pop(op.trade_id, None)
            if live is None:
                continue
            pnl = live.notional * live.ret
            pools[live.symbol] += live.notional + pnl
            trade_rows.append(
                {
                    "date": dt,
                    "trade_id": live.trade_id,
                    "symbol": live.symbol,
                    "model": "independent",
                    "status": "closed",
                    "notional": live.notional,
                    "pnl": pnl,
                    "ret": live.ret,
                    "score": live.score,
                }
            )

        day_entries = sorted(open_by_date.get(dt, []), key=lambda x: (-x.score, x.symbol))
        for op in day_entries:
            cash_sym = pools[op.symbol]
            alloc = cash_sym * float(trade_fraction)
            if alloc < float(min_notional):
                trade_rows.append(
                    {
                        "date": dt,
                        "trade_id": op.trade_id,
                        "symbol": op.symbol,
                        "model": "independent",
                        "status": "skipped_min_notional",
                        "notional": 0.0,
                        "pnl": 0.0,
                        "ret": op.ret,
                        "score": op.score,
                    }
                )
                continue
            pools[op.symbol] -= alloc
            active[op.trade_id] = LivePos(
                trade_id=op.trade_id,
                symbol=op.symbol,
                entry_date=op.entry_date,
                exit_date=op.exit_date,
                ret=op.ret,
                notional=float(alloc),
                score=op.score,
            )

        reserved = float(np.sum([p.notional for p in active.values()])) if active else 0.0
        cash_total = float(np.sum(list(pools.values())))
        eq_rows.append(
            {
                "date": dt,
                "model": "independent",
                "cash_available": cash_total,
                "notional_reserved": reserved,
                "equity": cash_total + reserved,
                "open_positions": len(active),
            }
        )

    return pd.DataFrame(trade_rows), pd.DataFrame(eq_rows)


def _simulate_shared_ranked(
    candidates: pd.DataFrame,
    starting_cash: float,
    trade_fraction: float,
    reserve_pct: float,
    max_per_trade_pct: float,
    max_per_symbol_pct: float,
    max_open_positions: int,
    min_notional: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cash = float(starting_cash)
    open_by_date: dict[pd.Timestamp, list[LivePos]] = {}
    close_by_date: dict[pd.Timestamp, list[LivePos]] = {}

    for r in candidates.itertuples(index=False):
        op = LivePos(
            trade_id=int(r.trade_id),
            symbol=str(r.symbol),
            entry_date=pd.Timestamp(r.entry_date),
            exit_date=pd.Timestamp(r.exit_date),
            ret=float(r.ret),
            notional=0.0,
            score=float(r.score),
        )
        open_by_date.setdefault(op.entry_date, []).append(op)
        close_by_date.setdefault(op.exit_date, []).append(op)

    dates = sorted(set(open_by_date.keys()) | set(close_by_date.keys()))
    active: dict[int, LivePos] = {}
    trade_rows: list[dict[str, Any]] = []
    eq_rows: list[dict[str, Any]] = []

    for dt in dates:
        for op in close_by_date.get(dt, []):
            live = active.pop(op.trade_id, None)
            if live is None:
                continue
            pnl = live.notional * live.ret
            cash += live.notional + pnl
            trade_rows.append(
                {
                    "date": dt,
                    "trade_id": live.trade_id,
                    "symbol": live.symbol,
                    "model": "shared_ranked",
                    "status": "closed",
                    "notional": live.notional,
                    "pnl": pnl,
                    "ret": live.ret,
                    "score": live.score,
                }
            )

        reserved = float(np.sum([p.notional for p in active.values()])) if active else 0.0
        equity_pre = cash + reserved
        reserve_cash = float(reserve_pct) * equity_pre
        deployable = max(cash - reserve_cash, 0.0)

        by_symbol_reserved: dict[str, float] = {}
        for p in active.values():
            by_symbol_reserved[p.symbol] = by_symbol_reserved.get(p.symbol, 0.0) + p.notional

        day_entries = sorted(open_by_date.get(dt, []), key=lambda x: (-x.score, x.symbol))
        for op in day_entries:
            if len(active) >= int(max_open_positions):
                trade_rows.append(
                    {
                        "date": dt,
                        "trade_id": op.trade_id,
                        "symbol": op.symbol,
                        "model": "shared_ranked",
                        "status": "skipped_max_open_positions",
                        "notional": 0.0,
                        "pnl": 0.0,
                        "ret": op.ret,
                        "score": op.score,
                    }
                )
                continue

            symbol_cap = float(max_per_symbol_pct) * equity_pre
            symbol_used = by_symbol_reserved.get(op.symbol, 0.0)
            symbol_room = max(symbol_cap - symbol_used, 0.0)
            if symbol_room <= 0:
                trade_rows.append(
                    {
                        "date": dt,
                        "trade_id": op.trade_id,
                        "symbol": op.symbol,
                        "model": "shared_ranked",
                        "status": "skipped_symbol_cap",
                        "notional": 0.0,
                        "pnl": 0.0,
                        "ret": op.ret,
                        "score": op.score,
                    }
                )
                continue

            trade_cap = float(max_per_trade_pct) * equity_pre
            alloc = min(trade_cap, symbol_room, deployable, cash, float(trade_fraction) * cash)
            if alloc < float(min_notional):
                trade_rows.append(
                    {
                        "date": dt,
                        "trade_id": op.trade_id,
                        "symbol": op.symbol,
                        "model": "shared_ranked",
                        "status": "skipped_cash_or_min_notional",
                        "notional": 0.0,
                        "pnl": 0.0,
                        "ret": op.ret,
                        "score": op.score,
                    }
                )
                continue

            cash -= alloc
            deployable -= alloc
            by_symbol_reserved[op.symbol] = by_symbol_reserved.get(op.symbol, 0.0) + alloc
            active[op.trade_id] = LivePos(
                trade_id=op.trade_id,
                symbol=op.symbol,
                entry_date=op.entry_date,
                exit_date=op.exit_date,
                ret=op.ret,
                notional=float(alloc),
                score=op.score,
            )

        reserved_post = float(np.sum([p.notional for p in active.values()])) if active else 0.0
        eq_rows.append(
            {
                "date": dt,
                "model": "shared_ranked",
                "cash_available": cash,
                "notional_reserved": reserved_post,
                "equity": cash + reserved_post,
                "open_positions": len(active),
            }
        )

    return pd.DataFrame(trade_rows), pd.DataFrame(eq_rows)


def _summarize_model(
    model_name: str,
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    starting_cash: float,
) -> dict[str, Any]:
    if equity.empty:
        return {
            "model": model_name,
            "starting_cash": starting_cash,
            "ending_equity": np.nan,
            "growth_pct": np.nan,
            "cagr_pct": np.nan,
            "executed_trades": 0,
            "skipped_trades": 0,
            "win_rate_pct": np.nan,
            "total_pnl": np.nan,
            "avg_pnl_per_trade": np.nan,
            "max_drawdown_pct": np.nan,
            "avg_open_positions": np.nan,
        }

    eq = equity.sort_values("date").reset_index(drop=True)
    end_equity = float(eq["equity"].iloc[-1])
    growth_pct = 100.0 * (end_equity / float(starting_cash) - 1.0)
    cagr_pct = _cagr_pct(eq["date"], eq["equity"])
    max_dd_pct = _max_drawdown_pct(eq["equity"])
    avg_open = float(eq["open_positions"].mean()) if "open_positions" in eq.columns else np.nan

    closed = trades[trades["status"] == "closed"].copy()
    skipped = trades[trades["status"].str.startswith("skipped")].copy()
    executed_trades = int(len(closed))
    skipped_trades = int(len(skipped))
    total_pnl = float(closed["pnl"].sum()) if executed_trades else 0.0
    avg_pnl = float(closed["pnl"].mean()) if executed_trades else 0.0
    win_rate_pct = 100.0 * float((closed["pnl"] > 0.0).mean()) if executed_trades else 0.0

    return {
        "model": model_name,
        "starting_cash": float(starting_cash),
        "ending_equity": end_equity,
        "growth_pct": growth_pct,
        "cagr_pct": cagr_pct,
        "executed_trades": executed_trades,
        "skipped_trades": skipped_trades,
        "win_rate_pct": win_rate_pct,
        "total_pnl": total_pnl,
        "avg_pnl_per_trade": avg_pnl,
        "max_drawdown_pct": max_dd_pct,
        "avg_open_positions": avg_open,
    }


def main() -> None:
    args = _parse_args()
    symbols = ex._parse_symbols(args)
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Day"

    conn = sqlite3.connect(str(Path(args.db_path)))
    daily = ex._read_daily(conn, source_key=source_key, symbols=symbols)
    conn.close()
    if daily.empty:
        raise RuntimeError("No daily data found in cache.")

    candidates = _build_candidates(
        daily=daily,
        breakout_window=int(args.breakout_window),
        range_mode=str(args.range_mode),
        setup_max_days=int(args.setup_max_days),
        eval_segment=str(args.eval_segment),
        train_ratio=float(args.train_ratio),
    )
    if candidates.empty:
        raise RuntimeError("No short candidates generated.")

    tr_ind, eq_ind = _simulate_independent(
        candidates=candidates,
        symbols=sorted(set(symbols)),
        cash_per_symbol=float(args.independent_cash_per_symbol),
        trade_fraction=float(args.independent_trade_fraction),
        min_notional=float(args.min_trade_notional),
    )
    start_ind = float(args.independent_cash_per_symbol) * float(len(symbols))
    s_ind = _summarize_model("independent_10k_each", tr_ind, eq_ind, start_ind)

    tr_sh, eq_sh = _simulate_shared_ranked(
        candidates=candidates,
        starting_cash=float(args.shared_starting_cash),
        trade_fraction=float(args.shared_trade_fraction),
        reserve_pct=float(args.shared_reserve_pct),
        max_per_trade_pct=float(args.shared_max_per_trade_pct),
        max_per_symbol_pct=float(args.shared_max_per_symbol_pct),
        max_open_positions=int(args.shared_max_open_positions),
        min_notional=float(args.min_trade_notional),
    )
    s_sh = _summarize_model("shared_100k_ranked", tr_sh, eq_sh, float(args.shared_starting_cash))

    # Normalize pnl for fair notional comparison.
    s_ind["pnl_per_100k"] = 100000.0 * float(s_ind["total_pnl"]) / max(float(s_ind["starting_cash"]), 1e-9)
    s_sh["pnl_per_100k"] = 100000.0 * float(s_sh["total_pnl"]) / max(float(s_sh["starting_cash"]), 1e-9)

    summary = pd.DataFrame([s_ind, s_sh])
    summary["eval_segment"] = str(args.eval_segment)
    summary["symbols_count"] = len(symbols)
    summary["candidate_trades"] = int(len(candidates))
    summary["date_start"] = str(pd.to_datetime(candidates["entry_date"]).min().date())
    summary["date_end"] = str(pd.to_datetime(candidates["entry_date"]).max().date())

    print("=== Short Capital Model Comparison ===")
    print(
        f"symbols={len(symbols)} eval_segment={args.eval_segment} "
        f"candidate_trades={len(candidates)} date_range={summary['date_start'].iloc[0]} to {summary['date_end'].iloc[0]}"
    )
    print(
        summary[
            [
                "model",
                "starting_cash",
                "ending_equity",
                "growth_pct",
                "cagr_pct",
                "total_pnl",
                "pnl_per_100k",
                "executed_trades",
                "skipped_trades",
                "avg_open_positions",
            ]
        ].to_string(index=False)
    )

    out_path = Path(args.save_summary_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_path, index=False)
    print(f"saved_summary_csv={out_path}")

    # Also save detail logs for audit.
    base = out_path.with_suffix("")
    tr_ind.to_csv(base.parent / f"{base.name}_trades_independent.csv", index=False)
    eq_ind.to_csv(base.parent / f"{base.name}_equity_independent.csv", index=False)
    tr_sh.to_csv(base.parent / f"{base.name}_trades_shared.csv", index=False)
    eq_sh.to_csv(base.parent / f"{base.name}_equity_shared.csv", index=False)


if __name__ == "__main__":
    main()
