from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pandas as pd

# -------------------------
# Config
# -------------------------

@dataclass(frozen=True)
class FallbackConfig:
    # Fallback starts after this time (Eastern)
    fallback_start_time: str = "10:30"

    # Eligibility thresholds (symbol-level) from ema_pivot_profile_universe.csv
    min_ema_respect_ratio: float = 0.65
    max_ema_violation_count: float = 12.0

    # EMA-only conservative parameters (same spirit as your earlier EMA-only module)
    ema_period: int = 20
    pullback_touch_band_perc: float = 0.15
    max_dist_from_ema_perc: float = 0.60
    ema_slope_min_perc: float = 0.02

    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 2

    swing_lookback_bars: int = 8
    stop_buffer_perc: float = 0.05

    ema_exit_confirm_closes: int = 2
    eod_exit_time: str = "15:55"

    one_trade_per_day: bool = True


# -------------------------
# IO helpers
# -------------------------

def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df

def read_bars(path: str) -> pd.DataFrame:
    df = load_csv(path)
    if df.empty:
        return df

    if "timestamp" not in df.columns:
        if df.columns[0].lower().startswith("unnamed"):
            df = df.rename(columns={df.columns[0]: "timestamp"})
        else:
            raise ValueError(f"Cannot find timestamp column in {path}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    df = df.sort_values("timestamp").set_index("timestamp")
    return df

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def compute_ema_slope_perc(ema: pd.Series, lookback: int = 5) -> pd.Series:
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
    flips = (s != s.shift(1)).sum()
    return int(max(flips - 1, 0))


# -------------------------
# Core: EMA-only fallback entry search (after fallback start time)
# -------------------------

def add_ema_columns(df: pd.DataFrame, cfg: FallbackConfig) -> pd.DataFrame:
    out = df.copy()
    if "ema20" not in out.columns:
        out["ema20"] = compute_ema(out["close"], cfg.ema_period)
    out["ema_slope_perc"] = compute_ema_slope_perc(out["ema20"], lookback=5).fillna(0.0)
    out["dist_from_ema_perc"] = (out["close"] - out["ema20"]) / out["ema20"].replace(0, pd.NA) * 100.0
    out["price_vs_ema_local"] = [price_vs_ema(c, e) for c, e in zip(out["close"], out["ema20"])]

    # Chop crosses (per-day rolling window)
    out["chop_ema_crosses"] = pd.NA
    for sd, day_df in out.groupby("session_date", sort=True):
        for ts in day_df.index:
            look = day_df.loc[:ts].tail(cfg.chop_lookback_bars)["price_vs_ema_local"]
            out.loc[ts, "chop_ema_crosses"] = ema_cross_count(look)
    return out

def ts_for_time(session_date: str, hhmm: str) -> pd.Timestamp:
    # bars are typically naive; treat as naive strings, compare by time component
    return pd.Timestamp(f"{session_date} {hhmm}")

def find_fallback_signal(day_df: pd.DataFrame, cfg: FallbackConfig, forced_direction: Optional[str]) -> Optional[pd.Timestamp]:
    """
    Returns timestamp of signal bar (the bar that triggers entry on next bar open).
    forced_direction:
      - "UP" or "DOWN" if ORB breakout exists that day (we require alignment)
      - None if no ORB breakout exists (EMA-only direction is allowed)
    """
    start_ts = ts_for_time(str(day_df["session_date"].iloc[0]), cfg.fallback_start_time)
    window = day_df[day_df.index >= start_ts].copy()
    if window.empty:
        return None

    touched = False
    active_dir = None  # LONG/SHORT
    armed = False

    for ts, r in window.iterrows():
        crosses = int(r["chop_ema_crosses"]) if pd.notna(r["chop_ema_crosses"]) else 0
        if crosses > cfg.chop_max_ema_crosses:
            touched = False
            active_dir = None
            armed = False
            continue

        slope = float(r["ema_slope_perc"])
        dist = float(r["dist_from_ema_perc"])
        pvs = str(r["price_vs_ema_local"])

        # Determine EMA-only direction
        if slope >= cfg.ema_slope_min_perc:
            active_dir = "LONG"
        elif slope <= -cfg.ema_slope_min_perc:
            active_dir = "SHORT"
        else:
            touched = False
            active_dir = None
            armed = False
            continue

        # Enforce ORB alignment if breakout exists
        if forced_direction == "UP" and active_dir != "LONG":
            continue
        if forced_direction == "DOWN" and active_dir != "SHORT":
            continue

        # Avoid chasing
        if abs(dist) > cfg.max_dist_from_ema_perc:
            continue

        armed = True

        if active_dir == "LONG":
            if (dist <= cfg.pullback_touch_band_perc) and (pvs in {"AT", "BELOW"}):
                touched = True
            if touched and pvs == "ABOVE":
                return ts

        if active_dir == "SHORT":
            if (dist >= -cfg.pullback_touch_band_perc) and (pvs in {"AT", "ABOVE"}):
                touched = True
            if touched and pvs == "BELOW":
                return ts

    return None


def simulate_fallback_trade(day_df: pd.DataFrame, signal_ts: pd.Timestamp, cfg: FallbackConfig, symbol: str) -> Optional[Dict[str, Any]]:
    pos = day_df.index.get_indexer([signal_ts])[0]
    if pos + 1 >= len(day_df):
        return None

    sig = day_df.loc[signal_ts]

    # Decide side from EMA slope sign at signal
    side = "LONG" if float(sig["ema_slope_perc"]) >= 0 else "SHORT"

    entry_ts = day_df.index[pos + 1]
    entry_price = float(day_df.loc[entry_ts]["open"])

    # Structural stop via recent swing
    lb = day_df.iloc[max(0, pos - cfg.swing_lookback_bars + 1):pos + 1]
    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)

    if side == "LONG":
        swing_low = float(lb["low"].min())
        stop_price = swing_low - buffer
        risk = entry_price - stop_price
    else:
        swing_high = float(lb["high"].max())
        stop_price = swing_high + buffer
        risk = stop_price - entry_price

    if risk <= 0:
        return None

    # Exit rules: stop OR 2 closes against EMA OR EOD
    ema_confirm = 0
    eod_ts = ts_for_time(str(sig["session_date"]), cfg.eod_exit_time)
    if eod_ts not in day_df.index:
        eod_ts = day_df.index[day_df.index <= eod_ts].max()

    mfe = 0.0
    mae = 0.0
    exit_ts = None
    exit_price = None
    exit_reason = None

    for ts, r in day_df.loc[entry_ts:].iterrows():
        if side == "LONG":
            mfe = max(mfe, float(r["high"]) - entry_price)
            mae = min(mae, float(r["low"]) - entry_price)

            if float(r["low"]) <= stop_price:
                exit_ts, exit_price, exit_reason = ts, stop_price, "STOP_HIT"
                break

            if float(r["close"]) < float(r["ema20"]):
                ema_confirm += 1
            else:
                ema_confirm = 0

        else:
            mfe = max(mfe, entry_price - float(r["low"]))
            mae = min(mae, entry_price - float(r["high"]))

            if float(r["high"]) >= stop_price:
                exit_ts, exit_price, exit_reason = ts, stop_price, "STOP_HIT"
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
            exit_ts, exit_price, exit_reason = ts, float(r["close"]), "EOD_EXIT"
            break

    if exit_ts is None:
        exit_ts, exit_price, exit_reason = day_df.index[-1], float(day_df.iloc[-1]["close"]), "FORCED_EXIT"

    pnl = (exit_price - entry_price) if side == "LONG" else (entry_price - exit_price)
    r_mult = pnl / risk

    return {
        "session_date": str(sig["session_date"]),
        "symbol": symbol,
        "setup_type": "ORB_FALLBACK",
        "signal_time": str(signal_ts),
        "entry_time": str(entry_ts),
        "side": side,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "risk_per_share": risk,
        "exit_time": str(exit_ts),
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "gross_pnl_per_share": pnl,
        "r_multiple": r_mult,
        "mfe_r": (mfe / risk) if risk else None,
        "mae_r": (mae / risk) if risk else None,
    }


def main():
    cfg = FallbackConfig()

    # Inputs
    base_con = os.path.join(os.getcwd(), "data", "research", "conservative")
    p1_profile_path = os.path.join(base_con, "ema_pivot_profile_universe.csv")

    # Outputs
    base_out = os.path.join(os.getcwd(), "data", "research", "orb_fallback")
    ensure_dir(base_out)

    profile = load_csv(p1_profile_path)
    if profile.empty:
        raise SystemExit(f"Missing {p1_profile_path} (run Module P1 first)")

    eligible = profile[
        (profile["avg_ema_respect_ratio"] >= cfg.min_ema_respect_ratio) &
        (profile["avg_ema_violation_count"] <= cfg.max_ema_violation_count)
    ]["symbol"].astype(str).tolist()

    print(f"[V3] Eligible symbols (P1 thresholds): {eligible}")

    # Iterate symbols in baseline folder
    symbols = sorted([d for d in os.listdir(base_con) if os.path.isdir(os.path.join(base_con, d)) and d not in {"ab_tests"}])

    for sym in symbols:
        out_dir = os.path.join(base_out, sym)
        ensure_dir(out_dir)

        # Load bars
        bars_path = os.path.join(base_con, sym, "bars_with_orb_ema.csv")
        bars = read_bars(bars_path)
        if bars.empty:
            print(f"❌ {sym}: missing bars_with_orb_ema.csv")
            continue

        bars = add_ema_columns(bars, cfg)

        # Load baseline trades to know if ORB traded that day
        base_trades = load_csv(os.path.join(base_con, sym, "trades.csv"))
        trade_days = set(base_trades["session_date"].astype(str)) if not base_trades.empty else set()

        trades: List[Dict[str, Any]] = []

        for sd, day_df in bars.groupby("session_date", sort=True):
            sd = str(sd)

            # If baseline already traded the day, do nothing (fallback not needed)
            if cfg.one_trade_per_day and sd in trade_days:
                continue

            # If symbol not eligible, skip fallback entirely
            if sym not in eligible:
                continue

            # Determine ORB direction if available; enforce alignment when present
            forced_direction = None
            if "orb_breakout" in day_df.columns:
                x = day_df["orb_breakout"].dropna()
                if not x.empty and str(x.iloc[0]) in {"UP", "DOWN"}:
                    forced_direction = str(x.iloc[0])

            sig_ts = find_fallback_signal(day_df, cfg, forced_direction=forced_direction)
            if sig_ts is None:
                continue

            t = simulate_fallback_trade(day_df, sig_ts, cfg, symbol=sym)
            if t:
                trades.append(t)

        trades_df = pd.DataFrame(trades)
        trades_df.to_csv(os.path.join(out_dir, "trades.csv"), index=False)
        print(f"✅ {sym}: fallback trades={len(trades_df)} -> {os.path.join(out_dir, 'trades.csv')}")

    print("\n✅ V3 generation complete: data/research/orb_fallback/<SYMBOL>/trades.csv")


if __name__ == "__main__":
    main()
