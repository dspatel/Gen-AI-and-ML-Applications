from __future__ import annotations

import os
import math
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pandas as pd


# -------------------------
# Config (EMA-only)
# -------------------------

@dataclass(frozen=True)
class EMAOnlyConfig:
    timezone: str = "America/New_York"

    ema_period: int = 20
    # Conservative pullback-reclaim around EMA
    pullback_touch_band_perc: float = 0.15
    max_dist_from_ema_perc: float = 0.60
    ema_slope_min_perc: float = 0.02

    # Chop filter
    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 2

    # Signal arming window (after a trend is established)
    # EMA-only uses “trend established” = EMA slope condition satisfied
    arm_window_bars: int = 24  # ~2 hours on 5m bars

    # Stop anchoring (EMA-only needs a structural stop not based on ORL/ORH)
    # We'll use a recent swing extreme + buffer
    swing_lookback_bars: int = 8
    stop_buffer_perc: float = 0.05

    # Exits (same philosophy as conservative)
    ema_exit_confirm_closes: int = 2
    eod_exit_time: str = "15:55"

    one_trade_per_day: bool = True


# -------------------------
# Utilities
# -------------------------

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def read_bars_with_orb_ema(path: str) -> pd.DataFrame:
    """
    Reads S4 bars_with_orb_ema.csv (may have timestamp as a column or unnamed index).
    Returns a DataFrame indexed by timestamp.
    """
    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return df

    if "timestamp" not in df.columns:
        # common case: index saved as unnamed col
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
    # Count regime flips in ABOVE/BELOW ignoring AT via forward-fill
    s = series.replace({"AT": pd.NA}).ffill()
    s = s[s.notna()]
    if len(s) < 2:
        return 0
    return int(max((s != s.shift(1)).sum() - 1, 0))


def eod_timestamp(session_date: str, cfg: EMAOnlyConfig) -> pd.Timestamp:
    return pd.Timestamp(f"{session_date} {cfg.eod_exit_time}", tz=cfg.timezone)


# -------------------------
# EMA-only signal generation
# -------------------------

def add_required_ema_columns(df: pd.DataFrame, cfg: EMAOnlyConfig) -> pd.DataFrame:
    out = df.copy()

    if "ema20" not in out.columns:
        out["ema20"] = compute_ema(out["close"], cfg.ema_period)

    if "ema_slope_perc" not in out.columns:
        out["ema_slope_perc"] = compute_ema_slope_perc(out["ema20"], lookback=5).fillna(0.0)

    if "dist_from_ema_perc" not in out.columns:
        out["dist_from_ema_perc"] = (out["close"] - out["ema20"]) / out["ema20"].replace(0, pd.NA) * 100.0

    if "price_vs_ema" not in out.columns:
        out["price_vs_ema"] = [price_vs_ema(c, e) for c, e in zip(out["close"], out["ema20"])]

    # Chop crosses per bar within each day
    out["chop_ema_crosses"] = pd.NA
    for sd, day_df in out.groupby("session_date", sort=True):
        for ts in day_df.index:
            look = day_df.loc[:ts].tail(cfg.chop_lookback_bars)["price_vs_ema"]
            out.loc[ts, "chop_ema_crosses"] = ema_cross_count(look)

    return out


def compute_ema_only_signals(bars: pd.DataFrame, cfg: EMAOnlyConfig) -> pd.DataFrame:
    """
    Conservative EMA-only:
    - Determine direction by EMA slope sign (>= +min -> LONG regime, <= -min -> SHORT regime)
    - Arm when regime becomes valid
    - Require pullback touch near EMA and reclaim to trigger entry
    - Stop uses recent swing extreme (lookback) +/- buffer
    """
    out = bars.copy()
    out["ema_only_trend_dir"] = pd.NA  # LONG/SHORT/NA
    out["ema_only_armed"] = False
    out["ema_only_entry_signal"] = False
    out["planned_entry_price"] = pd.NA
    out["planned_stop_price"] = pd.NA
    out["risk_per_share"] = pd.NA
    out["side"] = pd.NA
    out["signal_reason"] = pd.NA

    for sd, day_df in out.groupby("session_date", sort=True):
        if day_df.empty:
            continue

        traded = False

        # We scan the day sequentially
        touched = False
        armed_count = 0
        active_dir = None  # LONG/SHORT

        for ts, r in day_df.iterrows():
            if cfg.one_trade_per_day and traded:
                break

            crosses = int(r["chop_ema_crosses"]) if pd.notna(r["chop_ema_crosses"]) else 0
            if crosses > cfg.chop_max_ema_crosses:
                # too choppy -> do not arm
                touched = False
                armed_count = 0
                active_dir = None
                continue

            slope = float(r["ema_slope_perc"])
            dist = float(r["dist_from_ema_perc"])
            pvs = str(r["price_vs_ema"])

            # Determine trend direction
            if slope >= cfg.ema_slope_min_perc:
                active_dir = "LONG"
            elif slope <= -cfg.ema_slope_min_perc:
                active_dir = "SHORT"
            else:
                # no clear trend -> disarm
                active_dir = None
                touched = False
                armed_count = 0
                continue

            out.loc[ts, "ema_only_trend_dir"] = active_dir

            # Arm window logic: once trend is established, allow only next N bars to set up
            armed_count += 1
            if armed_count > cfg.arm_window_bars:
                # re-arm by restarting after window (keeps conservative)
                touched = False
                armed_count = 1

            # Distance constraint (avoid chasing far from EMA)
            if abs(dist) > cfg.max_dist_from_ema_perc:
                continue

            out.loc[ts, "ema_only_armed"] = True

            # Pullback + reclaim trigger
            if active_dir == "LONG":
                if (dist <= cfg.pullback_touch_band_perc) and (pvs in {"AT", "BELOW"}):
                    touched = True

                if touched and pvs == "ABOVE":
                    # Enter next bar open
                    pos = day_df.index.get_indexer([ts])[0]
                    if pos + 1 >= len(day_df):
                        break
                    entry_ts = day_df.index[pos + 1]
                    entry_price = float(day_df.loc[entry_ts]["open"])

                    # Stop = recent swing low - buffer
                    lb = day_df.iloc[max(0, pos - cfg.swing_lookback_bars + 1):pos + 1]
                    swing_low = float(lb["low"].min())
                    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                    stop_price = swing_low - buffer

                    risk = entry_price - stop_price
                    if risk <= 0:
                        continue

                    out.loc[ts, "ema_only_entry_signal"] = True
                    out.loc[ts, "side"] = "LONG"
                    out.loc[ts, "planned_entry_price"] = entry_price
                    out.loc[ts, "planned_stop_price"] = stop_price
                    out.loc[ts, "risk_per_share"] = risk
                    out.loc[ts, "signal_reason"] = "EMA_ONLY|EMA_TREND|PULLBACK_RECLAIM|SWING_STOP"
                    traded = True
                    break

            else:  # SHORT
                if (dist >= -cfg.pullback_touch_band_perc) and (pvs in {"AT", "ABOVE"}):
                    touched = True

                if touched and pvs == "BELOW":
                    pos = day_df.index.get_indexer([ts])[0]
                    if pos + 1 >= len(day_df):
                        break
                    entry_ts = day_df.index[pos + 1]
                    entry_price = float(day_df.loc[entry_ts]["open"])

                    # Stop = recent swing high + buffer
                    lb = day_df.iloc[max(0, pos - cfg.swing_lookback_bars + 1):pos + 1]
                    swing_high = float(lb["high"].max())
                    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                    stop_price = swing_high + buffer

                    risk = stop_price - entry_price
                    if risk <= 0:
                        continue

                    out.loc[ts, "ema_only_entry_signal"] = True
                    out.loc[ts, "side"] = "SHORT"
                    out.loc[ts, "planned_entry_price"] = entry_price
                    out.loc[ts, "planned_stop_price"] = stop_price
                    out.loc[ts, "risk_per_share"] = risk
                    out.loc[ts, "signal_reason"] = "EMA_ONLY|EMA_TREND|PULLBACK_RECLAIM|SWING_STOP"
                    traded = True
                    break

    return out


# -------------------------
# EMA-only trade simulation
# -------------------------

def simulate_trade(day_df: pd.DataFrame, signal_ts: pd.Timestamp, symbol: str, cfg: EMAOnlyConfig) -> Optional[Dict[str, Any]]:
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
        "setup_type": "EMA_ONLY",
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


def main():
    cfg = EMAOnlyConfig()

    base_in = os.path.join(os.getcwd(), "data", "research", "conservative")
    base_out = os.path.join(os.getcwd(), "data", "research", "ema_only")
    ensure_dir(base_out)

    symbols = [d for d in os.listdir(base_in) if os.path.isdir(os.path.join(base_in, d)) and d not in {"ab_tests"}]
    symbols = sorted(symbols)

    print("[EMA-only] symbols:", symbols)

    for sym in symbols:
        in_dir = os.path.join(base_in, sym)
        out_dir = os.path.join(base_out, sym)
        ensure_dir(out_dir)

        bars_path = os.path.join(in_dir, "bars_with_orb_ema.csv")
        bars = read_bars_with_orb_ema(bars_path)
        if bars.empty:
            print(f"❌ {sym}: missing/empty bars_with_orb_ema.csv")
            continue

        bars = add_required_ema_columns(bars, cfg)
        signals = compute_ema_only_signals(bars, cfg)

        signals_out = os.path.join(out_dir, "signals.csv")
        # Save with timestamp index
        signals.to_csv(signals_out, index=True)

        # Trades
        all_trades: List[Dict[str, Any]] = []
        for sd, day_df in signals.groupby("session_date", sort=True):
            sig_rows = day_df[day_df["ema_only_entry_signal"] == True]
            if sig_rows.empty:
                continue
            sig_ts = sig_rows.index[0]
            t = simulate_trade(day_df, sig_ts, sym, cfg)
            if t:
                all_trades.append(t)

        trades = pd.DataFrame(all_trades)
        trades_out = os.path.join(out_dir, "trades.csv")
        trades.to_csv(trades_out, index=False)

        print(f"✅ {sym}: signals -> {signals_out} | trades={len(trades)} -> {trades_out}")


if __name__ == "__main__":
    main()
