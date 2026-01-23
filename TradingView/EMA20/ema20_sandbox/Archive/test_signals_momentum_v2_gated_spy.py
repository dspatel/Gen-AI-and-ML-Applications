# ============================================================
# Module D-M v2: Momentum Signals (GATED continuation)
#
# Input:
#   data/SPY_30d_5m_yahoo_ema20_orb.csv
#
# Output:
#   data/SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v2.csv
#
# Goal:
#   Fix Momentum v1 overtrading by gating continuation entries:
#   - Strong EMA slope required
#   - Only trade within time window (avoid lunch chop)
#   - OR range must be large enough (avoid noise)
#   - Breakout close must be meaningfully beyond ORH/ORL
#
# Run:
#   python test_signals_momentum_v2_gated_spy.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Tuple

import pandas as pd


@dataclass(frozen=True)
class MomentumV2Config:
    timezone: str = "America/New_York"

    # --- GATES (the whole point of v2) ---
    # EMA slope is percent over your lookback window (already computed in your pipeline)
    ema_slope_min_perc: float = 0.03  # stronger than v1 (0.005)

    # Time window gate (local to timezone)
    trade_start_time: str = "10:00"
    trade_end_time: str = "11:30"

    # OR range minimum (% of price). Tiny OR ranges tend to chop.
    or_range_min_perc: float = 0.10

    # Breakout strength: require signal candle close beyond OR boundary by this %
    breakout_close_min_perc: float = 0.05

    # Extension filter (still useful)
    max_dist_from_ema_perc: float = 1.00

    # Chop filter (optional, but helpful)
    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 3

    # Stop (tight) around OR boundary with buffer
    stop_buffer_perc: float = 0.03

    # Trades/day: keep 1 for controlled testing
    max_trades_per_day: int = 1


def load_input(path: str, tz: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError("CSV must contain 'timestamp' column.")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["timestamp"] = df["timestamp"].dt.tz_convert(tz)
    df = df.set_index("timestamp").sort_index()

    required = [
        "session_date", "open", "high", "low", "close",
        "ema20", "ema_slope_perc", "dist_from_ema_perc", "price_vs_ema",
        "orh", "orl", "orb_locked", "orb_breakout", "orb_breakout_time",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    return df


def ema_cross_count(price_vs_ema_series: pd.Series) -> int:
    s = price_vs_ema_series.replace({"AT": pd.NA}).ffill()
    s = s[s.notna()]
    if len(s) < 2:
        return 0
    crosses = (s != s.shift(1)).sum() - 1
    return int(max(crosses, 0))


def within_time_window(ts: pd.Timestamp, start_hhmm: str, end_hhmm: str) -> bool:
    # Compare time component only (assumes ts is localized)
    t = ts.strftime("%H:%M")
    return (t >= start_hhmm) and (t <= end_hhmm)


def main():
    cfg = MomentumV2Config()

    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v2.csv")

    print("\n[Module D-M v2] Loading ORB+EMA CSV:")
    print(f"  {in_path}")
    df = load_input(in_path, cfg.timezone)

    out = df.copy()

    # outputs
    out["chop_ema_crosses"] = pd.NA
    out["trend_ok"] = False
    out["gates_ok"] = False
    out["entry_signal"] = False
    out["planned_entry_price"] = pd.NA
    out["planned_stop_price"] = pd.NA
    out["risk_per_share"] = pd.NA
    out["side"] = pd.NA
    out["signal_reason"] = pd.NA
    out["signal_type"] = pd.NA  # MOMENTUM_CONTINUATION_V2

    # precompute chop crosses
    for session_date, day_df in out.groupby("session_date", sort=True):
        for ts in day_df.index:
            look = day_df.loc[:ts].tail(cfg.chop_lookback_bars)["price_vs_ema"]
            out.loc[ts, "chop_ema_crosses"] = ema_cross_count(look)

    sessions = sorted(out["session_date"].unique())
    print(f"Rows: {len(out):,} | Sessions: {len(sessions)}")

    for session_date in sessions:
        day_df = out[out["session_date"] == session_date].copy()
        if day_df.empty:
            continue

        # Must have ORB breakout identified and locked
        btime_series = day_df["orb_breakout_time"].dropna()
        if btime_series.empty:
            continue

        breakout_time = pd.to_datetime(btime_series.iloc[0], utc=True, errors="coerce").tz_convert(cfg.timezone)

        bdir_rows = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
        if bdir_rows.empty:
            continue
        breakout_dir = str(bdir_rows["orb_breakout"].iloc[0])  # UP or DOWN

        # OR range gate
        orh = float(day_df["orh"].iloc[0])
        orl = float(day_df["orl"].iloc[0])
        # use breakout area price as baseline
        baseline_price = float(day_df.loc[day_df.index[0]]["open"])
        or_range_perc = ((orh - orl) / baseline_price) * 100.0 if baseline_price else 0.0
        if or_range_perc < cfg.or_range_min_perc:
            # Entire day fails OR-range gate
            continue

        trades_today = 0

        scan = day_df[day_df.index >= breakout_time].copy()
        if scan.empty:
            continue

        for ts, r in scan.iterrows():
            if trades_today >= cfg.max_trades_per_day:
                break

            # Locked
            if not bool(r["orb_locked"]):
                continue

            # Time window gate
            if not within_time_window(ts, cfg.trade_start_time, cfg.trade_end_time):
                continue

            # EMA slope gate
            slope = float(r["ema_slope_perc"])
            if breakout_dir == "UP":
                if slope < cfg.ema_slope_min_perc:
                    continue
            else:
                if slope > -cfg.ema_slope_min_perc:
                    continue

            # Chop gate
            crosses = int(out.loc[ts, "chop_ema_crosses"]) if pd.notna(out.loc[ts, "chop_ema_crosses"]) else 0
            if crosses > cfg.chop_max_ema_crosses:
                continue

            # Extension gate
            if abs(float(r["dist_from_ema_perc"])) > cfg.max_dist_from_ema_perc:
                continue

            # Breakout strength gate: close beyond OR boundary by breakout_close_min_perc
            close = float(r["close"])
            if breakout_dir == "UP":
                if close < (orh * (1.0 + cfg.breakout_close_min_perc / 100.0)):
                    continue
                # must also be above EMA to confirm trend continuation
                if str(r["price_vs_ema"]) != "ABOVE":
                    continue
            else:
                if close > (orl * (1.0 - cfg.breakout_close_min_perc / 100.0)):
                    continue
                if str(r["price_vs_ema"]) != "BELOW":
                    continue

            # Passed all gates -> generate entry signal, entry fills next bar open
            idx_pos = day_df.index.get_indexer([ts])[0]
            if idx_pos + 1 >= len(day_df):
                break
            entry_ts = day_df.index[idx_pos + 1]
            entry_price = float(day_df.loc[entry_ts]["open"])

            buffer = entry_price * (cfg.stop_buffer_perc / 100.0)

            if breakout_dir == "UP":
                side = "LONG"
                stop_price = orh - buffer
                risk = entry_price - stop_price
            else:
                side = "SHORT"
                stop_price = orl + buffer
                risk = stop_price - entry_price

            if risk <= 0:
                continue

            out.loc[ts, "trend_ok"] = True
            out.loc[ts, "gates_ok"] = True
            out.loc[ts, "entry_signal"] = True
            out.loc[ts, "side"] = side
            out.loc[ts, "planned_entry_price"] = entry_price
            out.loc[ts, "planned_stop_price"] = stop_price
            out.loc[ts, "risk_per_share"] = risk
            out.loc[ts, "signal_type"] = "MOMENTUM_CONTINUATION_V2"
            out.loc[ts, "signal_reason"] = (
                f"ORB_CONFIRMED|GATED_V2|SLOPE>={cfg.ema_slope_min_perc}"
                f"|OR_RANGE>={cfg.or_range_min_perc}%|BREAKOUT_CLOSE>={cfg.breakout_close_min_perc}%"
                f"|TIME={cfg.trade_start_time}-{cfg.trade_end_time}"
            )

            trades_today += 1
            break  # take first valid gated continuation entry

    out.to_csv(out_path, index=True)

    n = int(out["entry_signal"].sum())
    print(f"\n✅ Entry signals found (momentum v2 gated): {n}")
    if n > 0:
        cols = ["session_date", "side", "signal_type", "planned_entry_price", "planned_stop_price", "risk_per_share", "signal_reason"]
        print("\nFirst 10 signals:")
        print(out[out["entry_signal"]][cols].head(10).round(6))

    print(f"\nSaved momentum v2 signals to: {out_path}")
    print("\nNext: Run execution + decision-vs-reality + $10k sim using v2 files.\n")


if __name__ == "__main__":
    main()
