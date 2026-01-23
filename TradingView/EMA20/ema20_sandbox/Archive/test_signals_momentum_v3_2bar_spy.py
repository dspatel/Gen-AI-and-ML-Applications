# ============================================================
# Module D-M v3: Momentum Signals (GATED continuation + 2-bar confirmation)
#
# Input:
#   data/SPY_30d_5m_yahoo_ema20_orb.csv
#
# Output:
#   data/SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v3.csv
#
# What changed vs v2:
# - Requires TWO consecutive closes beyond ORH/ORL (with strength buffer)
# - Then enters on NEXT BAR OPEN after the 2nd confirming close.
#
# Run:
#   python test_signals_momentum_v3_2bar_spy.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class MomentumV3Config:
    timezone: str = "America/New_York"

    # --- GATES ---
    ema_slope_min_perc: float = 0.03          # strong trend only
    trade_start_time: str = "10:00"           # local to timezone
    trade_end_time: str = "11:30"
    or_range_min_perc: float = 0.10           # % of price
    breakout_close_min_perc: float = 0.05     # % beyond ORH/ORL per confirming close
    max_dist_from_ema_perc: float = 1.00
    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 3

    # --- 2-bar confirmation ---
    confirm_closes_required: int = 2          # v3 feature

    # --- Stops / risk ---
    stop_buffer_perc: float = 0.03            # % of entry price
    max_trades_per_day: int = 1


def within_time_window(ts: pd.Timestamp, start_hhmm: str, end_hhmm: str) -> bool:
    t = ts.strftime("%H:%M")
    return (t >= start_hhmm) and (t <= end_hhmm)


def ema_cross_count(price_vs_ema_series: pd.Series) -> int:
    s = price_vs_ema_series.replace({"AT": pd.NA}).ffill()
    s = s[s.notna()]
    if len(s) < 2:
        return 0
    crosses = (s != s.shift(1)).sum() - 1
    return int(max(crosses, 0))


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

    # ensure types
    df["orb_locked"] = df["orb_locked"].astype(bool)
    return df


def get_breakout_time_and_dir(day_df: pd.DataFrame, tz: str) -> tuple[Optional[pd.Timestamp], Optional[str]]:
    # breakout time
    b = day_df["orb_breakout_time"].dropna()
    if b.empty:
        return None, None
    bt = pd.to_datetime(b.iloc[0], utc=True, errors="coerce").tz_convert(tz)

    # breakout dir
    rows = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
    if rows.empty:
        return bt, None
    direction = str(rows["orb_breakout"].iloc[0])
    return bt, direction


def main():
    cfg = MomentumV3Config()

    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v3.csv")

    print("\n[Module D-M v3] Loading ORB+EMA CSV:")
    print(f"  {in_path}")
    df = load_input(in_path, cfg.timezone)

    out = df.copy()

    # outputs
    out["chop_ema_crosses"] = pd.NA
    out["trend_ok"] = False
    out["gates_ok"] = False
    out["confirm_count"] = 0
    out["entry_signal"] = False
    out["planned_entry_price"] = pd.NA
    out["planned_stop_price"] = pd.NA
    out["risk_per_share"] = pd.NA
    out["side"] = pd.NA
    out["signal_reason"] = pd.NA
    out["signal_type"] = pd.NA

    # precompute chop crosses per bar
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

        breakout_time, breakout_dir = get_breakout_time_and_dir(day_df, cfg.timezone)
        if breakout_time is None or breakout_dir is None:
            continue

        # OR range gate
        orh = float(day_df["orh"].iloc[0])
        orl = float(day_df["orl"].iloc[0])
        baseline_price = float(day_df.iloc[0]["open"])
        or_range_perc = ((orh - orl) / baseline_price) * 100.0 if baseline_price else 0.0
        if or_range_perc < cfg.or_range_min_perc:
            continue

        # scan after breakout time
        scan = day_df[day_df.index >= breakout_time].copy()
        if scan.empty:
            continue

        confirm = 0
        trades_today = 0

        for ts, r in scan.iterrows():
            if trades_today >= cfg.max_trades_per_day:
                break

            if not bool(r["orb_locked"]):
                continue

            # time window
            if not within_time_window(ts, cfg.trade_start_time, cfg.trade_end_time):
                confirm = 0
                continue

            # slope gate
            slope = float(r["ema_slope_perc"])
            if breakout_dir == "UP":
                if slope < cfg.ema_slope_min_perc:
                    confirm = 0
                    continue
            else:
                if slope > -cfg.ema_slope_min_perc:
                    confirm = 0
                    continue

            # chop gate
            crosses = int(out.loc[ts, "chop_ema_crosses"]) if pd.notna(out.loc[ts, "chop_ema_crosses"]) else 0
            if crosses > cfg.chop_max_ema_crosses:
                confirm = 0
                continue

            # extension gate
            if abs(float(r["dist_from_ema_perc"])) > cfg.max_dist_from_ema_perc:
                confirm = 0
                continue

            # price vs ema alignment gate
            pve = str(r["price_vs_ema"])
            if breakout_dir == "UP" and pve != "ABOVE":
                confirm = 0
                continue
            if breakout_dir == "DOWN" and pve != "BELOW":
                confirm = 0
                continue

            # --- 2-bar confirmation: close beyond OR boundary with buffer ---
            close = float(r["close"])
            if breakout_dir == "UP":
                thresh = orh * (1.0 + cfg.breakout_close_min_perc / 100.0)
                if close >= thresh:
                    confirm += 1
                else:
                    confirm = 0
            else:
                thresh = orl * (1.0 - cfg.breakout_close_min_perc / 100.0)
                if close <= thresh:
                    confirm += 1
                else:
                    confirm = 0

            out.loc[ts, "confirm_count"] = confirm

            if confirm < cfg.confirm_closes_required:
                continue

            # Confirmed on this bar -> enter next bar open
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
                confirm = 0
                continue

            out.loc[ts, "trend_ok"] = True
            out.loc[ts, "gates_ok"] = True
            out.loc[ts, "entry_signal"] = True
            out.loc[ts, "side"] = side
            out.loc[ts, "planned_entry_price"] = entry_price
            out.loc[ts, "planned_stop_price"] = stop_price
            out.loc[ts, "risk_per_share"] = risk
            out.loc[ts, "signal_type"] = "MOMENTUM_CONTINUATION_V3_2BAR"
            out.loc[ts, "signal_reason"] = (
                f"ORB_CONFIRMED|V3_2BAR|CONFIRM={cfg.confirm_closes_required}"
                f"|SLOPE>={cfg.ema_slope_min_perc}"
                f"|OR_RANGE>={cfg.or_range_min_perc}%|BREAKOUT_CLOSE>={cfg.breakout_close_min_perc}%"
                f"|TIME={cfg.trade_start_time}-{cfg.trade_end_time}"
            )

            trades_today += 1
            break

    out.to_csv(out_path, index=True)

    n = int(out["entry_signal"].sum())
    print(f"\n✅ Entry signals found (momentum v3 2-bar): {n}")
    if n > 0:
        cols = ["session_date", "side", "signal_type", "confirm_count", "planned_entry_price",
                "planned_stop_price", "risk_per_share", "signal_reason"]
        print("\nFirst 10 signals:")
        print(out[out["entry_signal"]][cols].head(10).round(6))

    print(f"\nSaved momentum v3 signals to: {out_path}\n")


if __name__ == "__main__":
    main()
