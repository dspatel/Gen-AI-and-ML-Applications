from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_SYMBOLS_FILE = Path(__file__).resolve().parent / "universes" / "focus_symbols_v1.txt"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "alpaca_daily_cache.sqlite"
DEFAULT_FOLDS_CSV = Path(__file__).resolve().parent / "reports" / "exit_opt_folds.csv"
DEFAULT_SUMMARY_CSV = Path(__file__).resolve().parent / "reports" / "exit_opt_summary.csv"


@dataclass
class Entry:
    symbol: str
    date: pd.Timestamp
    idx: int
    side: str
    entry_price: float
    breakout_window: int
    breakout_level: float
    ema20_entry: float


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Walk-forward exit optimizer for anchored/rolling daily EMA20 breakout entries. "
            "Entries are frozen; only exit logic is optimized on train and tested on unseen folds."
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
    p.add_argument("--side", choices=["long", "short", "both"], default="long")

    p.add_argument("--train-days", type=int, default=180)
    p.add_argument("--test-days", type=int, default=60)
    p.add_argument("--step-days", type=int, default=60)
    p.add_argument("--min-trades-train", type=int, default=20)
    p.add_argument("--min-trades-test", type=int, default=5)

    p.add_argument("--baseline-fixed-days", type=int, default=10)
    p.add_argument("--max-hold-cap", type=int, default=40)
    p.add_argument(
        "--candidate-profile",
        choices=["full", "short_focus", "short_conservative", "single_short_anchor"],
        default="full",
        help=(
            "Candidate set used for optimization. "
            "'single_short_anchor' locks to the current best short preset "
            "(hybrid atr_mult=1.5 hard_stop_atr=1.0 breakeven_r=0 max_hold=15 no ema flip)."
        ),
    )

    p.add_argument("--save-folds-csv", default=str(DEFAULT_FOLDS_CSV))
    p.add_argument("--save-summary-csv", default=str(DEFAULT_SUMMARY_CSV))
    return p.parse_args()


def _parse_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols.strip():
        vals = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
        return sorted(dict.fromkeys(vals))
    p = Path(args.symbols_file)
    if not p.exists():
        raise FileNotFoundError(f"Symbols file not found: {p}")
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        sym = t.split(":")[0].strip().upper()
        if sym:
            out.append(sym)
    return sorted(dict.fromkeys(out))


def _read_daily(conn: sqlite3.Connection, source_key: str, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    marks = ",".join(["?"] * len(symbols))
    q = f"""
        SELECT symbol, ts_utc, open, high, low, close, volume
        FROM daily_bars
        WHERE source_key = ? AND symbol IN ({marks})
        ORDER BY symbol, ts_utc
    """
    d = pd.read_sql_query(q, conn, params=[source_key] + symbols)
    if d.empty:
        return d
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True, errors="coerce")
    d = d.dropna(subset=["ts_utc"]).copy()
    d["date"] = d["ts_utc"].dt.tz_convert("America/New_York").dt.tz_localize(None)
    return d.sort_values(["symbol", "date"]).reset_index(drop=True)


def _add_base_features(z: pd.DataFrame) -> pd.DataFrame:
    x = z.sort_values("date").reset_index(drop=True).copy()
    x["ema20_d"] = x["close"].ewm(span=20, adjust=False).mean()
    x["prev_close"] = x["close"].shift(1)
    x["prev_ema20_d"] = x["ema20_d"].shift(1)
    x["cross_up_d"] = (x["prev_close"] <= x["prev_ema20_d"]) & (x["close"] > x["ema20_d"])
    x["cross_down_d"] = (x["prev_close"] >= x["prev_ema20_d"]) & (x["close"] < x["ema20_d"])

    prev_close = x["close"].shift(1)
    tr1 = x["high"] - x["low"]
    tr2 = (x["high"] - prev_close).abs()
    tr3 = (x["low"] - prev_close).abs()
    x["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    x["atr14"] = x["tr"].rolling(14, min_periods=14).mean()
    return x


def _build_entries_for_symbol(
    z: pd.DataFrame,
    symbol: str,
    breakout_window: int,
    range_mode: str,
    setup_max_days: int,
    side_mode: str,
) -> list[Entry]:
    x = _add_base_features(z)
    x["range_high"] = x["high"].shift(1).rolling(breakout_window, min_periods=breakout_window).max()
    x["range_low"] = x["low"].shift(1).rolling(breakout_window, min_periods=breakout_window).min()

    entries: list[Entry] = []
    up_expiry = -1
    down_expiry = -1
    anchor_high = np.nan
    anchor_low = np.nan
    regime = ""
    signal_used = False

    for i in range(len(x)):
        cross_up = bool(x.at[i, "cross_up_d"])
        cross_down = bool(x.at[i, "cross_down_d"])

        if range_mode == "rolling":
            if cross_up:
                up_expiry = i + int(setup_max_days)
            if cross_down:
                down_expiry = i + int(setup_max_days)
        else:
            if cross_up:
                hi = x.at[i, "range_high"]
                lo = x.at[i, "range_low"]
                if not pd.isna(hi) and not pd.isna(lo):
                    anchor_high = float(hi)
                    anchor_low = float(lo)
                    regime = "up"
                    signal_used = False
            if cross_down:
                hi = x.at[i, "range_high"]
                lo = x.at[i, "range_low"]
                if not pd.isna(hi) and not pd.isna(lo):
                    anchor_high = float(hi)
                    anchor_low = float(lo)
                    regime = "down"
                    signal_used = False

        close_i = float(x.at[i, "close"])
        ema_i = float(x.at[i, "ema20_d"]) if not pd.isna(x.at[i, "ema20_d"]) else np.nan
        if range_mode == "rolling":
            hi = x.at[i, "range_high"]
            lo = x.at[i, "range_low"]
        else:
            hi = anchor_high
            lo = anchor_low
        if pd.isna(hi) or pd.isna(lo):
            continue

        long_candidate = False
        short_candidate = False
        if range_mode == "rolling":
            long_candidate = (i <= up_expiry) and (close_i > float(hi))
            short_candidate = (i <= down_expiry) and (close_i < float(lo))
        else:
            long_candidate = (regime == "up") and (not signal_used) and (close_i > float(hi))
            short_candidate = (regime == "down") and (not signal_used) and (close_i < float(lo))

        if long_candidate and side_mode in ("long", "both"):
            entries.append(
                Entry(
                    symbol=symbol,
                    date=x.at[i, "date"],
                    idx=i,
                    side="long",
                    entry_price=close_i,
                    breakout_window=int(breakout_window),
                    breakout_level=float(hi),
                    ema20_entry=ema_i,
                )
            )
            if range_mode == "rolling":
                up_expiry = -1
            else:
                signal_used = True

        if short_candidate and side_mode in ("short", "both"):
            entries.append(
                Entry(
                    symbol=symbol,
                    date=x.at[i, "date"],
                    idx=i,
                    side="short",
                    entry_price=close_i,
                    breakout_window=int(breakout_window),
                    breakout_level=float(lo),
                    ema20_entry=ema_i,
                )
            )
            if range_mode == "rolling":
                down_expiry = -1
            else:
                signal_used = True
    return entries


def _build_symbol_frames(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym, g in daily.groupby("symbol"):
        out[str(sym)] = _add_base_features(g)
    return out


def _build_entries(
    daily: pd.DataFrame,
    breakout_window: int,
    range_mode: str,
    setup_max_days: int,
    side_mode: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for sym, g in daily.groupby("symbol"):
        for e in _build_entries_for_symbol(
            g,
            symbol=str(sym),
            breakout_window=breakout_window,
            range_mode=range_mode,
            setup_max_days=setup_max_days,
            side_mode=side_mode,
        ):
            rows.append(
                {
                    "symbol": e.symbol,
                    "date": e.date,
                    "idx": e.idx,
                    "side": e.side,
                    "entry_price": e.entry_price,
                    "breakout_window": e.breakout_window,
                    "breakout_level": e.breakout_level,
                    "ema20_entry": e.ema20_entry,
                }
            )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["date", "symbol", "idx"]).reset_index(drop=True)


def _fixed_exit(frame: pd.DataFrame, e: Entry, fixed_days: int) -> tuple[float, pd.Timestamp, str]:
    exit_idx = min(e.idx + int(fixed_days), len(frame) - 1)
    exit_px = float(frame.at[exit_idx, "close"])
    if e.side == "long":
        ret = exit_px / e.entry_price - 1.0
    else:
        ret = e.entry_price / exit_px - 1.0
    return float(ret), frame.at[exit_idx, "date"], "time"


def _ema_flip_exit(frame: pd.DataFrame, e: Entry, max_hold: int) -> tuple[float, pd.Timestamp, str]:
    end_idx = min(e.idx + int(max_hold), len(frame) - 1)
    reason = "time"
    exit_idx = end_idx
    for j in range(e.idx + 1, end_idx + 1):
        close_j = float(frame.at[j, "close"])
        ema_j = frame.at[j, "ema20_d"]
        if pd.isna(ema_j):
            continue
        if e.side == "long" and close_j < float(ema_j):
            exit_idx = j
            reason = "ema_flip"
            break
        if e.side == "short" and close_j > float(ema_j):
            exit_idx = j
            reason = "ema_flip"
            break
    exit_px = float(frame.at[exit_idx, "close"])
    if e.side == "long":
        ret = exit_px / e.entry_price - 1.0
    else:
        ret = e.entry_price / exit_px - 1.0
    return float(ret), frame.at[exit_idx, "date"], reason


def _atr_trail_exit(frame: pd.DataFrame, e: Entry, atr_mult: float, max_hold: int) -> tuple[float, pd.Timestamp, str]:
    end_idx = min(e.idx + int(max_hold), len(frame) - 1)
    entry_atr = frame.at[e.idx, "atr14"]
    if pd.isna(entry_atr):
        return _fixed_exit(frame, e, min(int(max_hold), 10))
    reason = "time"
    exit_idx = end_idx
    exit_px = float(frame.at[end_idx, "close"])

    if e.side == "long":
        highest = e.entry_price
        stop = highest - float(atr_mult) * float(entry_atr)
        for j in range(e.idx + 1, end_idx + 1):
            atr_j = frame.at[j, "atr14"]
            if pd.isna(atr_j):
                atr_j = entry_atr
            highest = max(highest, float(frame.at[j, "high"]))
            stop = max(stop, highest - float(atr_mult) * float(atr_j))
            if float(frame.at[j, "low"]) <= stop:
                exit_idx = j
                exit_px = float(stop)
                reason = "atr_stop"
                break
    else:
        lowest = e.entry_price
        stop = lowest + float(atr_mult) * float(entry_atr)
        for j in range(e.idx + 1, end_idx + 1):
            atr_j = frame.at[j, "atr14"]
            if pd.isna(atr_j):
                atr_j = entry_atr
            lowest = min(lowest, float(frame.at[j, "low"]))
            stop = min(stop, lowest + float(atr_mult) * float(atr_j))
            if float(frame.at[j, "high"]) >= stop:
                exit_idx = j
                exit_px = float(stop)
                reason = "atr_stop"
                break

    if reason == "time":
        exit_px = float(frame.at[exit_idx, "close"])
    if e.side == "long":
        ret = exit_px / e.entry_price - 1.0
    else:
        ret = e.entry_price / exit_px - 1.0
    return float(ret), frame.at[exit_idx, "date"], reason


def _hybrid_exit(
    frame: pd.DataFrame,
    e: Entry,
    atr_mult: float,
    hard_stop_atr: float,
    breakeven_r: float,
    use_ema_flip: bool,
    max_hold: int,
) -> tuple[float, pd.Timestamp, str]:
    end_idx = min(e.idx + int(max_hold), len(frame) - 1)
    entry_atr = frame.at[e.idx, "atr14"]
    if pd.isna(entry_atr):
        return _fixed_exit(frame, e, min(int(max_hold), 10))

    reason = "time"
    exit_idx = end_idx
    exit_px = float(frame.at[end_idx, "close"])

    if e.side == "long":
        initial_stop = max(e.breakout_level, e.entry_price - float(hard_stop_atr) * float(entry_atr))
        risk_r = max(e.entry_price - initial_stop, 1e-6)
        highest = e.entry_price
        stop = initial_stop

        for j in range(e.idx + 1, end_idx + 1):
            close_j = float(frame.at[j, "close"])
            low_j = float(frame.at[j, "low"])
            atr_j = frame.at[j, "atr14"]
            if pd.isna(atr_j):
                atr_j = entry_atr
            highest = max(highest, float(frame.at[j, "high"]))
            trail_stop = highest - float(atr_mult) * float(atr_j)
            stop = max(stop, trail_stop)
            if (highest - e.entry_price) >= float(breakeven_r) * risk_r:
                stop = max(stop, e.entry_price)
            if low_j <= stop:
                exit_idx = j
                exit_px = float(stop)
                reason = "hybrid_stop"
                break
            if use_ema_flip:
                ema_j = frame.at[j, "ema20_d"]
                if not pd.isna(ema_j) and close_j < float(ema_j):
                    exit_idx = j
                    exit_px = close_j
                    reason = "ema_flip"
                    break
    else:
        # Short stop must start above/at entry, not below it.
        initial_stop = max(e.breakout_level, e.entry_price + float(hard_stop_atr) * float(entry_atr))
        risk_r = max(initial_stop - e.entry_price, 1e-6)
        lowest = e.entry_price
        stop = initial_stop

        for j in range(e.idx + 1, end_idx + 1):
            close_j = float(frame.at[j, "close"])
            high_j = float(frame.at[j, "high"])
            atr_j = frame.at[j, "atr14"]
            if pd.isna(atr_j):
                atr_j = entry_atr
            lowest = min(lowest, float(frame.at[j, "low"]))
            trail_stop = lowest + float(atr_mult) * float(atr_j)
            stop = min(stop, trail_stop)
            if (e.entry_price - lowest) >= float(breakeven_r) * risk_r:
                stop = min(stop, e.entry_price)
            if high_j >= stop:
                exit_idx = j
                exit_px = float(stop)
                reason = "hybrid_stop"
                break
            if use_ema_flip:
                ema_j = frame.at[j, "ema20_d"]
                if not pd.isna(ema_j) and close_j > float(ema_j):
                    exit_idx = j
                    exit_px = close_j
                    reason = "ema_flip"
                    break

    if reason == "time":
        exit_px = float(frame.at[exit_idx, "close"])
    if e.side == "long":
        ret = exit_px / e.entry_price - 1.0
    else:
        ret = e.entry_price / exit_px - 1.0
    return float(ret), frame.at[exit_idx, "date"], reason


def _eval_trade(frame: pd.DataFrame, e_row: pd.Series, cfg: dict[str, Any]) -> tuple[float, pd.Timestamp, str]:
    e = Entry(
        symbol=str(e_row.symbol),
        date=pd.Timestamp(e_row.date),
        idx=int(e_row.idx),
        side=str(e_row.side),
        entry_price=float(e_row.entry_price),
        breakout_window=int(e_row.breakout_window),
        breakout_level=float(e_row.breakout_level),
        ema20_entry=float(e_row.ema20_entry) if not pd.isna(e_row.ema20_entry) else np.nan,
    )

    kind = str(cfg["exit_type"])
    if kind == "fixed":
        return _fixed_exit(frame, e, int(cfg["fixed_days"]))
    if kind == "ema_flip":
        return _ema_flip_exit(frame, e, int(cfg["max_hold"]))
    if kind == "atr_trail":
        return _atr_trail_exit(frame, e, float(cfg["atr_mult"]), int(cfg["max_hold"]))
    if kind == "hybrid":
        return _hybrid_exit(
            frame,
            e,
            atr_mult=float(cfg["atr_mult"]),
            hard_stop_atr=float(cfg["hard_stop_atr"]),
            breakeven_r=float(cfg["breakeven_r"]),
            use_ema_flip=bool(cfg["use_ema_flip"]),
            max_hold=int(cfg["max_hold"]),
        )
    raise ValueError(f"Unknown exit config: {cfg}")


def _max_drawdown_from_returns(rets: list[float]) -> float:
    if not rets:
        return 0.0
    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        eq *= max(1.0 + float(r), 1e-12)
        peak = max(peak, eq)
        dd = 0.0 if peak <= 0 else (peak - eq) / peak
        max_dd = max(max_dd, dd)
    return float(max_dd)


def _evaluate_entries(
    entries: pd.DataFrame,
    symbol_frames: dict[str, pd.DataFrame],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    if entries.empty:
        return {
            "trades": 0,
            "hit_rate": np.nan,
            "avg_ret": np.nan,
            "median_ret": np.nan,
            "total_ret": 0.0,
            "comp_ret": 0.0,
            "max_dd": 0.0,
            "score": -1e18,
        }

    records: list[tuple[pd.Timestamp, float]] = []
    for r in entries.itertuples(index=False):
        frame = symbol_frames.get(str(r.symbol))
        if frame is None:
            continue
        if int(r.idx) >= len(frame) - 1:
            continue
        ret, exit_date, _ = _eval_trade(frame, pd.Series(r._asdict()), cfg)
        records.append((pd.Timestamp(exit_date), float(ret)))

    if not records:
        return {
            "trades": 0,
            "hit_rate": np.nan,
            "avg_ret": np.nan,
            "median_ret": np.nan,
            "total_ret": 0.0,
            "comp_ret": 0.0,
            "max_dd": 0.0,
            "score": -1e18,
        }

    rr = pd.DataFrame(records, columns=["exit_date", "ret"]).sort_values("exit_date").reset_index(drop=True)
    rets = rr["ret"].astype(float).tolist()
    trades = len(rets)
    hit = float(np.mean([x > 0.0 for x in rets])) if trades else np.nan
    avg_ret = float(np.mean(rets)) if trades else np.nan
    med_ret = float(np.median(rets)) if trades else np.nan
    total_ret = float(np.sum(rets)) if trades else 0.0
    comp_ret = float(np.prod([1.0 + x for x in rets]) - 1.0) if trades else 0.0
    max_dd = _max_drawdown_from_returns(rets)
    score = comp_ret - 0.5 * max_dd

    return {
        "trades": trades,
        "hit_rate": hit,
        "avg_ret": avg_ret,
        "median_ret": med_ret,
        "total_ret": total_ret,
        "comp_ret": comp_ret,
        "max_dd": max_dd,
        "score": score,
    }


def _candidate_grid(max_hold_cap: int, profile: str = "full") -> list[dict[str, Any]]:
    if profile == "single_short_anchor":
        cfg = {
            "exit_type": "hybrid",
            "atr_mult": 1.5,
            "hard_stop_atr": 1.0,
            "breakeven_r": 0.0,
            "use_ema_flip": False,
            "max_hold": 15,
        }
        if int(cfg["max_hold"]) <= int(max_hold_cap):
            return [cfg]
        cfg["max_hold"] = int(max_hold_cap)
        return [cfg]

    if profile == "short_focus":
        cands: list[dict[str, Any]] = []
        for d in [7, 10, 15, 20]:
            if d <= max_hold_cap:
                cands.append({"exit_type": "fixed", "fixed_days": int(d)})

        for mh in [10, 15, 20]:
            if mh <= max_hold_cap:
                cands.append({"exit_type": "ema_flip", "max_hold": int(mh)})

        for atr_mult in [1.25, 1.5, 1.75, 2.0]:
            for mh in [10, 15, 20]:
                if mh <= max_hold_cap:
                    cands.append({"exit_type": "atr_trail", "atr_mult": float(atr_mult), "max_hold": int(mh)})

        for atr_mult in [1.25, 1.5, 1.75]:
            for hard_stop_atr in [0.75, 1.0, 1.25]:
                for be_r in [0.0, 0.5]:
                    for use_ema in [False, True]:
                        for mh in [10, 15, 20]:
                            if mh <= max_hold_cap:
                                cands.append(
                                    {
                                        "exit_type": "hybrid",
                                        "atr_mult": float(atr_mult),
                                        "hard_stop_atr": float(hard_stop_atr),
                                        "breakeven_r": float(be_r),
                                        "use_ema_flip": bool(use_ema),
                                        "max_hold": int(mh),
                                    }
                                )
        return cands

    if profile == "short_conservative":
        cands: list[dict[str, Any]] = []
        for d in [7, 10, 15]:
            if d <= max_hold_cap:
                cands.append({"exit_type": "fixed", "fixed_days": int(d)})

        for atr_mult in [1.5, 1.75, 2.0]:
            for mh in [10, 15, 20]:
                if mh <= max_hold_cap:
                    cands.append({"exit_type": "atr_trail", "atr_mult": float(atr_mult), "max_hold": int(mh)})

        for atr_mult in [1.25, 1.5, 1.75]:
            for hard_stop_atr in [0.75, 1.0]:
                for mh in [10, 15, 20]:
                    if mh <= max_hold_cap:
                        cands.append(
                            {
                                "exit_type": "hybrid",
                                "atr_mult": float(atr_mult),
                                "hard_stop_atr": float(hard_stop_atr),
                                "breakeven_r": 0.0,
                                "use_ema_flip": False,
                                "max_hold": int(mh),
                            }
                        )
        return cands

    cands: list[dict[str, Any]] = []

    for d in [5, 7, 10, 15, 20]:
        if d <= max_hold_cap:
            cands.append({"exit_type": "fixed", "fixed_days": int(d)})

    for mh in [10, 15, 20, 30]:
        if mh <= max_hold_cap:
            cands.append({"exit_type": "ema_flip", "max_hold": int(mh)})

    for atr_mult in [1.5, 2.0, 2.5, 3.0]:
        for mh in [10, 15, 20, 30]:
            if mh <= max_hold_cap:
                cands.append({"exit_type": "atr_trail", "atr_mult": float(atr_mult), "max_hold": int(mh)})

    for atr_mult in [1.5, 2.0, 2.5]:
        for hard_stop_atr in [1.0, 1.5]:
            for be_r in [0.0, 1.0]:
                for use_ema in [False, True]:
                    for mh in [15, 20, 30, 40]:
                        if mh <= max_hold_cap:
                            cands.append(
                                {
                                    "exit_type": "hybrid",
                                    "atr_mult": float(atr_mult),
                                    "hard_stop_atr": float(hard_stop_atr),
                                    "breakeven_r": float(be_r),
                                    "use_ema_flip": bool(use_ema),
                                    "max_hold": int(mh),
                                }
                            )
    return cands


def _run_walk_forward(
    entries: pd.DataFrame,
    symbol_frames: dict[str, pd.DataFrame],
    calendar_dates: list[pd.Timestamp],
    train_days: int,
    test_days: int,
    step_days: int,
    min_trades_train: int,
    min_trades_test: int,
    baseline_cfg: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> pd.DataFrame:
    if entries.empty:
        return pd.DataFrame()
    dts = [pd.Timestamp(x).normalize() for x in calendar_dates]
    if len(dts) < (train_days + test_days):
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    fold = 0
    i = 0
    while i + train_days + test_days <= len(dts):
        train_set = set(dts[i : i + train_days])
        test_set = set(dts[i + train_days : i + train_days + test_days])
        tr = entries[entries["date"].dt.normalize().isin(train_set)].copy()
        te = entries[entries["date"].dt.normalize().isin(test_set)].copy()

        if len(tr) >= int(min_trades_train) and len(te) >= int(min_trades_test):
            baseline_train = _evaluate_entries(tr, symbol_frames, baseline_cfg)
            baseline_test = _evaluate_entries(te, symbol_frames, baseline_cfg)

            best_cfg = None
            best_train = None
            for cfg in candidates:
                m = _evaluate_entries(tr, symbol_frames, cfg)
                if m["trades"] < int(min_trades_train):
                    continue
                if best_train is None or m["score"] > best_train["score"]:
                    best_train = m
                    best_cfg = cfg

            if best_cfg is not None and best_train is not None:
                best_test = _evaluate_entries(te, symbol_frames, best_cfg)
                rows.append(
                    {
                        "fold": fold,
                        "train_start": dts[i],
                        "train_end": dts[i + train_days - 1],
                        "test_start": dts[i + train_days],
                        "test_end": dts[i + train_days + test_days - 1],
                        "train_trades": int(len(tr)),
                        "test_trades": int(len(te)),
                        "best_exit_cfg": json.dumps(best_cfg, sort_keys=True),
                        "best_train_score": float(best_train["score"]),
                        "best_train_comp_ret_pct": 100.0 * float(best_train["comp_ret"]),
                        "best_train_max_dd_pct": 100.0 * float(best_train["max_dd"]),
                        "best_test_comp_ret_pct": 100.0 * float(best_test["comp_ret"]),
                        "best_test_hit_rate_pct": 100.0 * float(best_test["hit_rate"]),
                        "best_test_avg_ret_pct": 100.0 * float(best_test["avg_ret"]),
                        "best_test_total_ret_pct": 100.0 * float(best_test["total_ret"]),
                        "best_test_max_dd_pct": 100.0 * float(best_test["max_dd"]),
                        "baseline_exit_cfg": json.dumps(baseline_cfg, sort_keys=True),
                        "baseline_test_comp_ret_pct": 100.0 * float(baseline_test["comp_ret"]),
                        "baseline_test_hit_rate_pct": 100.0 * float(baseline_test["hit_rate"]),
                        "baseline_test_avg_ret_pct": 100.0 * float(baseline_test["avg_ret"]),
                        "baseline_test_total_ret_pct": 100.0 * float(baseline_test["total_ret"]),
                        "baseline_test_max_dd_pct": 100.0 * float(baseline_test["max_dd"]),
                        "test_comp_edge_pct": 100.0 * (float(best_test["comp_ret"]) - float(baseline_test["comp_ret"])),
                    }
                )
        fold += 1
        i += int(step_days)
    return pd.DataFrame(rows)


def _summarize_folds(folds: pd.DataFrame) -> pd.DataFrame:
    if folds.empty:
        return pd.DataFrame()
    total_edge = folds["best_test_total_ret_pct"] - folds["baseline_test_total_ret_pct"]
    avg_edge = folds["best_test_avg_ret_pct"] - folds["baseline_test_avg_ret_pct"]
    out = pd.DataFrame(
        [
            {
                "folds": int(len(folds)),
                "median_best_test_comp_ret_pct": float(folds["best_test_comp_ret_pct"].median()),
                "median_baseline_test_comp_ret_pct": float(folds["baseline_test_comp_ret_pct"].median()),
                "mean_test_comp_edge_pct": float(folds["test_comp_edge_pct"].mean()),
                "median_test_comp_edge_pct": float(folds["test_comp_edge_pct"].median()),
                "mean_test_total_ret_edge_pct": float(total_edge.mean()),
                "median_test_total_ret_edge_pct": float(total_edge.median()),
                "mean_test_avg_ret_edge_pct": float(avg_edge.mean()),
                "median_test_avg_ret_edge_pct": float(avg_edge.median()),
                "win_folds_vs_baseline_pct": 100.0 * float(np.mean(folds["test_comp_edge_pct"] > 0.0)),
                "median_best_test_hit_rate_pct": float(folds["best_test_hit_rate_pct"].median()),
                "median_best_test_max_dd_pct": float(folds["best_test_max_dd_pct"].median()),
            }
        ]
    )
    return out


def main() -> None:
    args = _parse_args()
    symbols = _parse_symbols(args)
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Day"

    conn = sqlite3.connect(str(Path(args.db_path)))
    daily = _read_daily(conn, source_key=source_key, symbols=symbols)
    conn.close()
    if daily.empty:
        raise RuntimeError(f"No cached daily bars for source_key={source_key} and symbols={len(symbols)}.")

    entries = _build_entries(
        daily=daily,
        breakout_window=int(args.breakout_window),
        range_mode=str(args.range_mode),
        setup_max_days=int(args.setup_max_days),
        side_mode=str(args.side),
    )
    if entries.empty:
        raise RuntimeError("No entries generated. Try another breakout window/side/range mode.")

    symbol_frames = _build_symbol_frames(daily)
    calendar_dates = sorted(pd.to_datetime(daily["date"]).dt.normalize().unique().tolist())
    baseline_cfg = {"exit_type": "fixed", "fixed_days": int(args.baseline_fixed_days)}
    candidates = _candidate_grid(max_hold_cap=int(args.max_hold_cap), profile=str(args.candidate_profile))
    folds = _run_walk_forward(
        entries=entries,
        symbol_frames=symbol_frames,
        calendar_dates=calendar_dates,
        train_days=int(args.train_days),
        test_days=int(args.test_days),
        step_days=int(args.step_days),
        min_trades_train=int(args.min_trades_train),
        min_trades_test=int(args.min_trades_test),
        baseline_cfg=baseline_cfg,
        candidates=candidates,
    )
    if folds.empty:
        raise RuntimeError("No valid folds. Increase date span or lower min-trades thresholds.")

    summary = _summarize_folds(folds)

    print("=== Exit Optimization Setup ===")
    print(
        f"symbols={len(symbols)} bars={len(daily)} entries={len(entries)} "
        f"window={args.breakout_window} side={args.side} range_mode={args.range_mode}"
    )
    print(
        f"train_days={args.train_days} test_days={args.test_days} step_days={args.step_days} "
        f"min_train={args.min_trades_train} min_test={args.min_trades_test}"
    )
    print(f"candidate_profile={args.candidate_profile}")
    print(f"baseline={baseline_cfg}")
    print(f"candidates={len(candidates)}")

    print("\n=== Fold Results (head) ===")
    print(
        folds[
            [
                "fold",
                "train_start",
                "train_end",
                "test_start",
                "test_end",
                "best_exit_cfg",
                "best_test_comp_ret_pct",
                "baseline_test_comp_ret_pct",
                "test_comp_edge_pct",
            ]
        ]
        .head(12)
        .to_string(index=False)
    )

    print("\n=== Summary ===")
    print(summary.to_string(index=False))

    folds_path = Path(args.save_folds_csv)
    summary_path = Path(args.save_summary_csv)
    folds_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    folds.to_csv(folds_path, index=False)
    summary.to_csv(summary_path, index=False)
    print(f"\nSaved folds CSV: {folds_path}")
    print(f"Saved summary CSV: {summary_path}")


if __name__ == "__main__":
    main()
