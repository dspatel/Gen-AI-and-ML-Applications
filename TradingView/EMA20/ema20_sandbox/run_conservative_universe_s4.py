# ============================================================
# Module S4: Conservative pipeline across multiple symbols
# (ORB + EMA20 + Signals + Execution + Decision-vs-Reality + Equity)
#
# Inputs:
#   data/symbols.csv
#   data/canonical/<SYMBOL>_30d_5m_canonical.csv   (Module S3)
#
# Outputs (per symbol):
#   data/research/conservative/<SYMBOL>/bars_with_orb_ema.csv
#   data/research/conservative/<SYMBOL>/signals.csv
#   data/research/conservative/<SYMBOL>/trades.csv
#   data/research/conservative/<SYMBOL>/decision_vs_reality.csv
#   data/research/conservative/<SYMBOL>/equity_curve_10k.csv
#
# Outputs (universe):
#   data/research/conservative/universe_summary.csv
#
# Run (all steps):
#   python run_conservative_universe_s4.py
#
# Run a subset:
#   python run_conservative_universe_s4.py --steps orb
#   python run_conservative_universe_s4.py --steps orb,signals
#   python run_conservative_universe_s4.py --steps exec
#
# ============================================================

from __future__ import annotations

import os
import math
import argparse
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd

from symbols_loader import load_symbols_csv


# -------------------------
# Config
# -------------------------

@dataclass(frozen=True)
class S4Config:
    timezone: str = "America/New_York"
    bar_interval: str = "5m"

    # ORB
    orb_start: str = "09:30"
    orb_end: str = "10:00"  # first 30 mins
    breakout_confirm_closes: int = 1  # keep simple for research; production ORB keeps your confirm+rearm logic

    # EMA
    ema_period: int = 20

    # Conservative signals (pullback + reclaim)
    pullback_touch_band_perc: float = 0.15
    max_dist_from_ema_perc: float = 0.60
    ema_slope_min_perc: float = 0.02
    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 2
    pullback_arm_window_bars: int = 18  # 90 mins

    # Execution
    ema_exit_confirm_closes: int = 2
    eod_exit_time: str = "15:55"
    stop_buffer_perc: float = 0.05
    one_trade_per_day: bool = True

    # Decision vs reality
    horizon_bars: int = 12
    ref_stop_buffer_perc: float = 0.02

    # Equity
    start_capital: float = 10_000.0
    risk_pct: float = 0.01
    compounding: bool = True


# -------------------------
# Helpers
# -------------------------

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def load_canonical(symbol: str, path: str, tz: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Canonical file not found for {symbol}: {path}")

    df = pd.read_csv(path)
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    # canonical files from S3 store tz-aware strings; pandas reads them as aware in most cases
    # If not aware, localize to NY
    if getattr(df["timestamp"].dt, "tz", None) is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(tz)

    df = df.sort_values("timestamp").set_index("timestamp")
    return df


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_ema_slope_perc(ema: pd.Series, lookback: int = 5) -> pd.Series:
    # slope as percent change over lookback bars
    shifted = ema.shift(lookback)
    return (ema - shifted) / shifted.replace(0, pd.NA) * 100.0


def price_vs_ema(close: float, ema: float, eps: float = 1e-9) -> str:
    if abs(close - ema) <= eps:
        return "AT"
    return "ABOVE" if close > ema else "BELOW"


def ema_cross_count(series: pd.Series) -> int:
    s = series.replace({"AT": pd.NA}).ffill()
    s = s[s.notna()]
    if len(s) < 2:
        return 0
    return int(max((s != s.shift(1)).sum() - 1, 0))


# -------------------------
# Step ORB + EMA (bars_with_orb_ema)
# -------------------------

def add_orb_ema_features(df: pd.DataFrame, cfg: S4Config) -> pd.DataFrame:
    out = df.copy()

    # EMA
    out["ema20"] = compute_ema(out["close"], cfg.ema_period)
    out["ema_slope_perc"] = compute_ema_slope_perc(out["ema20"], lookback=5).fillna(0.0)
    out["dist_from_ema_perc"] = (out["close"] - out["ema20"]) / out["ema20"].replace(0, pd.NA) * 100.0
    out["price_vs_ema"] = [price_vs_ema(c, e) for c, e in zip(out["close"], out["ema20"])]

    # ORB columns
    out["orh"] = pd.NA
    out["orl"] = pd.NA
    out["orb_locked"] = False
    out["orb_breakout"] = pd.NA         # UP / DOWN / NA
    out["orb_breakout_time"] = pd.NA    # timestamp

    # Chop crosses per bar (within day)
    out["chop_ema_crosses"] = pd.NA

    for session_date, day_df in out.groupby("session_date", sort=True):
        if day_df.empty:
            continue

        # ORB window for that session_date
        # Use timestamp index already in NY tz
        hhmm = day_df.index.strftime("%H:%M")
        orb_mask = (hhmm >= cfg.orb_start) & (hhmm < cfg.orb_end)
        orb_df = day_df[orb_mask]

        if orb_df.empty:
            continue

        orh = float(orb_df["high"].max())
        orl = float(orb_df["low"].min())

        # lock time = orb_end
        lock_mask = (hhmm >= cfg.orb_end)
        locked_idx = day_df[lock_mask].index

        # set orh/orl for whole day
        out.loc[day_df.index, "orh"] = orh
        out.loc[day_df.index, "orl"] = orl
        out.loc[locked_idx, "orb_locked"] = True

        # breakout detection after ORB lock
        post = day_df[lock_mask]
        breakout_dir = None
        breakout_time = None
        for ts, r in post.iterrows():
            # confirmation close beyond OR level
            if breakout_dir is None:
                if float(r["close"]) > orh:
                    breakout_dir = "UP"
                    breakout_time = ts
                elif float(r["close"]) < orl:
                    breakout_dir = "DOWN"
                    breakout_time = ts

        if breakout_dir is not None:
            out.loc[day_df.index, "orb_breakout"] = breakout_dir
            out.loc[day_df.index, "orb_breakout_time"] = breakout_time

        # chop crosses rolling in-day
        for ts in day_df.index:
            look = day_df.loc[:ts].tail(cfg.chop_lookback_bars)["price_vs_ema"]
            out.loc[ts, "chop_ema_crosses"] = ema_cross_count(look)

    return out


# -------------------------
# Step Signals (Conservative)
# -------------------------

def compute_conservative_signals(bars: pd.DataFrame, cfg: S4Config) -> pd.DataFrame:
    out = bars.copy()

    out["trend_ok"] = False
    out["setup_armed"] = False
    out["entry_signal"] = False
    out["planned_entry_price"] = pd.NA
    out["planned_stop_price"] = pd.NA
    out["risk_per_share"] = pd.NA
    out["side"] = pd.NA
    out["signal_reason"] = pd.NA

    for session_date, day_df in out.groupby("session_date", sort=True):
        btime = day_df["orb_breakout_time"].dropna()
        if btime.empty:
            continue

        breakout_time = pd.to_datetime(btime.iloc[0])
        bdir_rows = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
        if bdir_rows.empty:
            continue
        breakout_dir = str(bdir_rows["orb_breakout"].iloc[0])

        # trade limit
        traded = False

        scan = day_df[day_df.index >= breakout_time].copy()
        if scan.empty:
            continue

        touched = False
        arm_end = min(len(scan), cfg.pullback_arm_window_bars + 1)
        arm = scan.iloc[:arm_end]

        for ts, r in arm.iterrows():
            if cfg.one_trade_per_day and traded:
                break

            if not bool(r["orb_locked"]):
                continue

            crosses = int(r["chop_ema_crosses"]) if pd.notna(r["chop_ema_crosses"]) else 0
            if crosses > cfg.chop_max_ema_crosses:
                continue

            dist = float(r["dist_from_ema_perc"])
            if abs(dist) > cfg.max_dist_from_ema_perc:
                continue

            slope = float(r["ema_slope_perc"])
            if breakout_dir == "UP" and slope < cfg.ema_slope_min_perc:
                continue
            if breakout_dir == "DOWN" and slope > -cfg.ema_slope_min_perc:
                continue

            out.loc[ts, "trend_ok"] = True
            out.loc[ts, "setup_armed"] = True

            pvs = str(r["price_vs_ema"])

            # Pullback touch + reclaim
            if breakout_dir == "UP":
                if (dist <= cfg.pullback_touch_band_perc) and (pvs in {"AT", "BELOW"}):
                    touched = True
                if touched and pvs == "ABOVE":
                    pos = day_df.index.get_indexer([ts])[0]
                    if pos + 1 >= len(day_df):
                        break
                    entry_ts = day_df.index[pos + 1]
                    entry_price = float(day_df.loc[entry_ts]["open"])

                    # stop below ORL with buffer
                    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                    stop_price = float(day_df["orl"].iloc[0]) - buffer
                    risk = entry_price - stop_price
                    if risk <= 0:
                        continue

                    out.loc[ts, "entry_signal"] = True
                    out.loc[ts, "side"] = "LONG"
                    out.loc[ts, "planned_entry_price"] = entry_price
                    out.loc[ts, "planned_stop_price"] = stop_price
                    out.loc[ts, "risk_per_share"] = risk
                    out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND|PULLBACK_RECLAIM"
                    traded = True
                    break

            else:
                if (dist >= -cfg.pullback_touch_band_perc) and (pvs in {"AT", "ABOVE"}):
                    touched = True
                if touched and pvs == "BELOW":
                    pos = day_df.index.get_indexer([ts])[0]
                    if pos + 1 >= len(day_df):
                        break
                    entry_ts = day_df.index[pos + 1]
                    entry_price = float(day_df.loc[entry_ts]["open"])

                    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                    stop_price = float(day_df["orh"].iloc[0]) + buffer
                    risk = stop_price - entry_price
                    if risk <= 0:
                        continue

                    out.loc[ts, "entry_signal"] = True
                    out.loc[ts, "side"] = "SHORT"
                    out.loc[ts, "planned_entry_price"] = entry_price
                    out.loc[ts, "planned_stop_price"] = stop_price
                    out.loc[ts, "risk_per_share"] = risk
                    out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND|PULLBACK_RECLAIM"
                    traded = True
                    break

    return out


# -------------------------
# Step Execution (Conservative)
# -------------------------

def eod_timestamp(session_date: str, cfg: S4Config) -> pd.Timestamp:
    return pd.Timestamp(f"{session_date} {cfg.eod_exit_time}", tz=cfg.timezone)


def simulate_trade(day_df: pd.DataFrame, signal_ts: pd.Timestamp, symbol: str, cfg: S4Config) -> Optional[Dict[str, Any]]:
    sig = day_df.loc[signal_ts]
    side = str(sig["side"]).upper()
    if side not in {"LONG", "SHORT"}:
        return None

    pos = day_df.index.get_indexer([signal_ts])[0]
    if pos + 1 >= len(day_df):
        return None

    entry_ts = day_df.index[pos + 1]
    entry_price = float(day_df.loc[entry_ts]["open"])

    stop_price = float(sig["planned_stop_price"])
    risk = float(sig["risk_per_share"])
    if risk <= 0:
        return None

    ema_confirm = 0
    mfe = 0.0
    mae = 0.0

    exit_ts = None
    exit_price = None
    exit_reason = None

    eod_ts = eod_timestamp(str(sig["session_date"]), cfg)
    if eod_ts not in day_df.index:
        eod_ts = day_df.index[day_df.index <= eod_ts].max()

    for ts, r in day_df.loc[entry_ts:].iterrows():
        if side == "LONG":
            mfe = max(mfe, float(r["high"]) - entry_price)
            mae = min(mae, float(r["low"]) - entry_price)

            if float(r["low"]) <= stop_price:
                exit_ts = ts
                exit_price = stop_price
                exit_reason = "STOP_HIT"
                break

            if float(r["close"]) < float(r["ema20"]):
                ema_confirm += 1
            else:
                ema_confirm = 0

        else:
            mfe = max(mfe, entry_price - float(r["low"]))
            mae = min(mae, entry_price - float(r["high"]))

            if float(r["high"]) >= stop_price:
                exit_ts = ts
                exit_price = stop_price
                exit_reason = "STOP_HIT"
                break

            if float(r["close"]) > float(r["ema20"]):
                ema_confirm += 1
            else:
                ema_confirm = 0

        if ema_confirm >= cfg.ema_exit_confirm_closes:
            p = day_df.index.get_indexer([ts])[0]
            if p + 1 < len(day_df):
                exit_ts = day_df.index[p + 1]
                exit_price = float(day_df.loc[exit_ts]["open"])
            else:
                exit_ts = ts
                exit_price = float(r["close"])
            exit_reason = "EMA_EXIT"
            break

        if ts == eod_ts:
            exit_ts = ts
            exit_price = float(r["close"])
            exit_reason = "EOD_EXIT"
            break

    if exit_ts is None:
        exit_ts = day_df.index[-1]
        exit_price = float(day_df.iloc[-1]["close"])
        exit_reason = "FORCED_EXIT"

    pnl = (exit_price - entry_price) if side == "LONG" else (entry_price - exit_price)
    r_mult = pnl / risk

    return {
        "session_date": str(sig["session_date"]),
        "symbol": symbol,
        "side": side,
        "setup_type": "CONSERVATIVE",
        "signal_time": signal_ts,
        "entry_time": entry_ts,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "risk_per_share": risk,
        "exit_time": exit_ts,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "gross_pnl_per_share": pnl,
        "r_multiple": r_mult,
        "mfe_r": (mfe / risk) if risk else None,
        "mae_r": (mae / risk) if risk else None,
        "signal_reason": str(sig.get("signal_reason", "")),
    }


# -------------------------
# Step Decision vs Reality
# -------------------------

def compute_future_mfe_mae_r(
    day_df: pd.DataFrame,
    start_ts: pd.Timestamp,
    side: str,
    entry_price: float,
    stop_price: float,
    horizon_bars: int
) -> Tuple[float, float, Optional[str]]:
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return 0.0, 0.0, None

    start_pos = day_df.index.get_indexer([start_ts])[0]
    end_pos = min(start_pos + horizon_bars, len(day_df) - 1)
    window = day_df.iloc[start_pos:end_pos + 1]

    mfe = 0.0
    mae = 0.0
    first_hit = None

    if side == "LONG":
        target_price = entry_price + risk
        stop_hit_price = entry_price - risk
    else:
        target_price = entry_price - risk
        stop_hit_price = entry_price + risk

    for _, r in window.iterrows():
        hi = float(r["high"])
        lo = float(r["low"])

        if side == "LONG":
            mfe = max(mfe, (hi - entry_price) / risk)
            mae = min(mae, (lo - entry_price) / risk)
            hit_target = hi >= target_price
            hit_stop = lo <= stop_hit_price
        else:
            mfe = max(mfe, (entry_price - lo) / risk)
            mae = min(mae, (entry_price - hi) / risk)
            hit_target = lo <= target_price
            hit_stop = hi >= stop_hit_price

        if first_hit is None:
            if hit_target and hit_stop:
                first_hit = "AMBIGUOUS"
            elif hit_target:
                first_hit = "PLUS_1R"
            elif hit_stop:
                first_hit = "MINUS_1R"

    return float(mfe), float(mae), first_hit


def classify_outcome(took_trade: bool, first_hit: Optional[str]) -> str:
    if first_hit is None:
        return "NO_DECISIVE_MOVE"
    if took_trade:
        if first_hit == "PLUS_1R":
            return "CORRECT_TRADE"
        if first_hit == "MINUS_1R":
            return "WRONG_TRADE"
        return "AMBIGUOUS_TRADE"
    else:
        if first_hit == "PLUS_1R":
            return "MISSED_OPPORTUNITY"
        if first_hit == "MINUS_1R":
            return "CORRECT_SKIP"
        return "AMBIGUOUS_SKIP"


def decision_vs_reality(
    bars: pd.DataFrame,
    trades: pd.DataFrame,
    symbol: str,
    cfg: S4Config
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    sessions = sorted(bars["session_date"].unique())

    for sd in sessions:
        day_df = bars[bars["session_date"] == sd].copy()
        if day_df.empty:
            continue

        btime = day_df["orb_breakout_time"].dropna()
        bdir = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
        if btime.empty or bdir.empty:
            continue

        breakout_time = pd.to_datetime(btime.iloc[0])
        direction = str(bdir["orb_breakout"].iloc[0])

        day_trade = None
        if not trades.empty:
            dt = trades[trades["session_date"] == sd].copy()
            if not dt.empty:
                day_trade = dt.sort_values("entry_time").iloc[0]

        took_trade = day_trade is not None

        if took_trade:
            side = str(day_trade["side"]).upper()
            entry_time = pd.to_datetime(day_trade["entry_time"])
            entry_price = float(day_trade["entry_price"])
            stop_price = float(day_trade["stop_price"])
            plan_type = "ACTUAL_TRADE"
        else:
            post = day_df[day_df.index >= breakout_time]
            if post.empty:
                continue
            entry_time = post.index[0]
            entry_price = float(post.iloc[0]["open"])
            buffer = entry_price * (cfg.ref_stop_buffer_perc / 100.0)
            if direction == "UP":
                side = "LONG"
                stop_price = float(day_df["orl"].iloc[0]) - buffer
            else:
                side = "SHORT"
                stop_price = float(day_df["orh"].iloc[0]) + buffer
            plan_type = "ORB_REFERENCE"

        mfe_r, mae_r, first_hit = compute_future_mfe_mae_r(
            day_df=day_df,
            start_ts=entry_time,
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            horizon_bars=cfg.horizon_bars
        )
        outcome = classify_outcome(took_trade, first_hit)

        rows.append({
            "symbol": symbol,
            "session_date": sd,
            "thesis_direction": direction,
            "took_trade": took_trade,
            "plan_type": plan_type,
            "breakout_time": breakout_time,
            "entry_time_ref": entry_time,
            "side_ref": side,
            "entry_price_ref": entry_price,
            "stop_price_ref": stop_price,
            "future_horizon_bars": cfg.horizon_bars,
            "future_mfe_r": mfe_r,
            "future_mae_r": mae_r,
            "first_hit": first_hit,
            "outcome_class": outcome,
        })

    return pd.DataFrame(rows).sort_values(["session_date"])


# -------------------------
# Step Equity (10k)
# -------------------------

def equity_curve_10k(trades: pd.DataFrame, cfg: S4Config) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["trade_index", "equity", "drawdown", "drawdown_pct"])

    equity = cfg.start_capital
    peak = equity
    rows = []

    trades = trades.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], errors="coerce")
    trades = trades.sort_values("entry_time").reset_index(drop=True)

    for i, r in trades.iterrows():
        risk_per_share = float(r["risk_per_share"])
        pnl_per_share = float(r["gross_pnl_per_share"])
        if not (risk_per_share > 0):
            continue

        base_equity = equity if cfg.compounding else cfg.start_capital
        risk_dollars = base_equity * cfg.risk_pct
        shares = max(1, math.floor(risk_dollars / risk_per_share))

        trade_pnl = pnl_per_share * shares
        equity += trade_pnl
        peak = max(peak, equity)
        dd = peak - equity

        rows.append({
            "trade_index": i,
            "session_date": r["session_date"],
            "shares": shares,
            "trade_pnl": trade_pnl,
            "equity": equity,
            "peak_equity": peak,
            "drawdown": dd,
            "drawdown_pct": (dd / peak) if peak else 0.0,
            "r_multiple": r["r_multiple"],
            "exit_reason": r["exit_reason"],
        })

    return pd.DataFrame(rows)


# -------------------------
# Universe summary
# -------------------------

def profit_factor(trades: pd.DataFrame) -> Optional[float]:
    if trades.empty:
        return None
    gp = trades.loc[trades["gross_pnl_per_share"] > 0, "gross_pnl_per_share"].sum()
    gl = trades.loc[trades["gross_pnl_per_share"] < 0, "gross_pnl_per_share"].abs().sum()
    if gl == 0:
        return None
    return float(gp / gl)


def summarize_symbol(symbol: str, trades: pd.DataFrame, eq: pd.DataFrame) -> Dict[str, Any]:
    if trades.empty:
        return {
            "symbol": symbol,
            "trades": 0,
            "total_r": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "final_equity_10k": None,
            "max_drawdown_pct": None,
        }

    wins = int((trades["r_multiple"] > 0).sum())
    n = int(len(trades))
    win_rate = wins / n if n else None

    final_equity = float(eq["equity"].iloc[-1]) if not eq.empty else None
    max_dd_pct = float(eq["drawdown_pct"].max()) * 100.0 if not eq.empty else None

    return {
        "symbol": symbol,
        "trades": n,
        "total_r": float(trades["r_multiple"].sum()),
        "win_rate": float(win_rate) if win_rate is not None else None,
        "profit_factor": profit_factor(trades),
        "final_equity_10k": final_equity,
        "max_drawdown_pct": max_dd_pct,
    }


# -------------------------
# Main driver
# -------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--steps",
        default="orb,signals,exec,reality,equity,summary",
        help="Comma-separated steps: orb,signals,exec,reality,equity,summary",
    )
    args = parser.parse_args()
    steps = {s.strip().lower() for s in args.steps.split(",") if s.strip()}

    cfg = S4Config()

    symbols_csv = os.path.join(os.getcwd(), "data", "symbols.csv")
    df_symbols = load_symbols_csv(symbols_csv, enabled_only=True)
    symbols = df_symbols["symbol"].tolist()

    base_in = os.path.join(os.getcwd(), "data", "canonical")
    base_out = os.path.join(os.getcwd(), "data", "research", "conservative")
    ensure_dir(base_out)

    universe_rows: List[Dict[str, Any]] = []

    print("\n[Module S4] Symbols:", symbols)
    print("[Module S4] Steps:", steps)

    for sym in symbols:
        sym_dir = os.path.join(base_out, sym)
        ensure_dir(sym_dir)

        canon_path = os.path.join(base_in, f"{sym}_30d_5m_canonical.csv")
        bars_path = os.path.join(sym_dir, "bars_with_orb_ema.csv")
        signals_path = os.path.join(sym_dir, "signals.csv")
        trades_path = os.path.join(sym_dir, "trades.csv")
        reality_path = os.path.join(sym_dir, "decision_vs_reality.csv")
        equity_path = os.path.join(sym_dir, "equity_curve_10k.csv")

        bars = None
        signals = None
        trades = None
        reality = None
        equity = None

        # ORB+EMA
        if "orb" in steps:
            df = load_canonical(sym, canon_path, cfg.timezone)
            if df.empty:
                print(f"\n{sym}: ❌ empty canonical file")
                continue
            bars = add_orb_ema_features(df, cfg)
            bars.to_csv(bars_path, index=True)
            print(f"\n{sym}: ✅ bars_with_orb_ema saved -> {bars_path}")

        # Signals
        if "signals" in steps:
            if bars is None:
                bars = pd.read_csv(bars_path)
                bars["timestamp"] = pd.to_datetime(bars["timestamp"])
                bars = bars.set_index("timestamp")
            signals = compute_conservative_signals(bars, cfg)
            # Keep full file (bars + signals flags) for debugging
            signals.to_csv(signals_path, index=True)
            print(f"{sym}: ✅ signals saved -> {signals_path}")

        # Execution
        if "exec" in steps:
            if signals is None:
                signals = pd.read_csv(signals_path)
                signals["timestamp"] = pd.to_datetime(signals["timestamp"])
                signals = signals.set_index("timestamp")

            all_trades: List[Dict[str, Any]] = []
            for sd, day_df in signals.groupby("session_date", sort=True):
                sig_rows = day_df[day_df["entry_signal"] == True]
                if sig_rows.empty:
                    continue
                sig_ts = pd.to_datetime(sig_rows.iloc[0]["timestamp"]) if "timestamp" in sig_rows.columns else sig_rows.index[0]
                # prefer index timestamps
                sig_ts = sig_rows.index[0]
                t = simulate_trade(day_df, sig_ts, sym, cfg)
                if t:
                    all_trades.append(t)

            trades = pd.DataFrame(all_trades)
            trades.to_csv(trades_path, index=False)
            print(f"{sym}: ✅ trades saved -> {trades_path} (n={len(trades)})")

        # Reality
        if "reality" in steps:
            if bars is None:
                bars = pd.read_csv(bars_path)
                bars["timestamp"] = pd.to_datetime(bars["timestamp"])
                bars = bars.set_index("timestamp")

            if trades is None:
                trades = pd.read_csv(trades_path) if os.path.exists(trades_path) else pd.DataFrame()

            reality = decision_vs_reality(bars, trades, sym, cfg)
            reality.to_csv(reality_path, index=False)
            print(f"{sym}: ✅ decision_vs_reality saved -> {reality_path} (rows={len(reality)})")

        # Equity
        if "equity" in steps:
            if trades is None:
                trades = pd.read_csv(trades_path) if os.path.exists(trades_path) else pd.DataFrame()

            equity = equity_curve_10k(trades, cfg)
            equity.to_csv(equity_path, index=False)
            print(f"{sym}: ✅ equity_curve_10k saved -> {equity_path} (rows={len(equity)})")

        # Universe summary row (needs trades+equity)
        if "summary" in steps:
            if trades is None:
                trades = pd.read_csv(trades_path) if os.path.exists(trades_path) else pd.DataFrame()
            if equity is None:
                equity = pd.read_csv(equity_path) if os.path.exists(equity_path) else pd.DataFrame()

            universe_rows.append(summarize_symbol(sym, trades, equity))

    if "summary" in steps:
        summary_df = pd.DataFrame(universe_rows)
        summary_df = summary_df.sort_values(["trades", "total_r"], ascending=[False, False])
        summary_path = os.path.join(base_out, "universe_summary.csv")
        summary_df.to_csv(summary_path, index=False)

        print("\n✅ Universe summary saved ->", summary_path)
        print("\nTop 10 by total_r:")
        print(summary_df.head(10))

        print("\nTop 10 by trades:")
        print(summary_df.sort_values(["trades", "total_r"], ascending=[False, False]).head(10))

        total_trades = int(summary_df["trades"].sum()) if not summary_df.empty else 0
        print(f"\nTotal trades across universe: {total_trades}")

    print("\n[Module S4] Done.\n")


if __name__ == "__main__":
    main()
