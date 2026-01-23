# ============================================================
# Module D-M: Momentum Signals + Trade Plan (ORB + EMA20)
#
# Input:
#   data/SPY_30d_5m_yahoo_ema20_orb.csv
#
# Output:
#   data/SPY_30d_5m_yahoo_ema20_orb_signals_momentum.csv
#
# Momentum behavior:
# - After ORB breakout confirmed, allow CONTINUATION entry (no pullback required)
# - Entry fill modeled as NEXT BAR OPEN after signal bar
# - Stop is tighter: around OR boundary with small buffer
# ============================================================

from __future__ import annotations
import os
from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class MomentumConfig:
    timezone: str = "America/New_York"

    # Trend filter (lighter than conservative)
    ema_slope_min_perc: float = 0.005      # percent over lookback (looser)
    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 4          # allow more chop than conservative
    max_dist_from_ema_perc: float = 1.00   # allow more extension

    # Entry
    allow_continuation_entry: bool = True
    allow_pullback_entry: bool = True
    pullback_touch_band_perc: float = 0.15
    pullback_arm_window_bars: int = 18     # 90 mins

    # Stop (tight)
    stop_buffer_perc: float = 0.03         # smaller buffer than conservative

    # Trade limit
    max_trades_per_day: int = 2            # momentum can take more


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
        "session_date","open","high","low","close",
        "ema20","ema_slope_perc","dist_from_ema_perc","price_vs_ema",
        "orh","orl","orb_locked","orb_breakout","orb_breakout_time"
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


def compute_signals(df: pd.DataFrame, cfg: MomentumConfig) -> pd.DataFrame:
    out = df.copy()

    # outputs
    out["chop_ema_crosses"] = pd.NA
    out["trend_ok"] = False
    out["setup_armed"] = False
    out["entry_signal"] = False
    out["planned_entry_price"] = pd.NA
    out["planned_stop_price"] = pd.NA
    out["risk_per_share"] = pd.NA
    out["side"] = pd.NA
    out["signal_reason"] = pd.NA
    out["signal_type"] = pd.NA  # CONTINUATION or PULLBACK

    # precompute chop crosses per day
    for session_date, day_df in out.groupby("session_date", sort=True):
        for ts in day_df.index:
            look = day_df.loc[:ts].tail(cfg.chop_lookback_bars)["price_vs_ema"]
            out.loc[ts, "chop_ema_crosses"] = ema_cross_count(look)

    for session_date, day_df in out.groupby("session_date", sort=True):
        # breakout
        btime = day_df["orb_breakout_time"].dropna()
        if btime.empty:
            continue
        breakout_time = pd.to_datetime(btime.iloc[0], utc=True, errors="coerce").tz_convert(cfg.timezone)

        bdir_rows = day_df[day_df["orb_breakout"].isin(["UP","DOWN"])]
        if bdir_rows.empty:
            continue
        breakout_dir = str(bdir_rows["orb_breakout"].iloc[0])

        trades_today = 0

        # start scanning from breakout time onward
        scan = day_df[day_df.index >= breakout_time].copy()
        if scan.empty:
            continue

        def trend_ok(ts: pd.Timestamp) -> bool:
            slope = float(day_df.loc[ts]["ema_slope_perc"])
            if breakout_dir == "UP":
                return slope >= cfg.ema_slope_min_perc
            else:
                return slope <= -cfg.ema_slope_min_perc

        def filters_ok(ts: pd.Timestamp) -> bool:
            if not bool(day_df.loc[ts]["orb_locked"]):
                return False
            if not trend_ok(ts):
                return False
            crosses = int(out.loc[ts, "chop_ema_crosses"]) if pd.notna(out.loc[ts, "chop_ema_crosses"]) else 0
            if crosses > cfg.chop_max_ema_crosses:
                return False
            if abs(float(day_df.loc[ts]["dist_from_ema_perc"])) > cfg.max_dist_from_ema_perc:
                return False
            return True

        # 1) CONTINUATION entry
        if cfg.allow_continuation_entry and trades_today < cfg.max_trades_per_day:
            for ts, r in scan.iterrows():
                if trades_today >= cfg.max_trades_per_day:
                    break
                if not filters_ok(ts):
                    continue

                pvs = str(r["price_vs_ema"])
                if breakout_dir == "UP" and pvs != "ABOVE":
                    continue
                if breakout_dir == "DOWN" and pvs != "BELOW":
                    continue

                # signal candle identified; entry next bar open
                pos = day_df.index.get_indexer([ts])[0]
                if pos + 1 >= len(day_df):
                    break
                entry_ts = day_df.index[pos + 1]
                entry_price = float(day_df.loc[entry_ts]["open"])
                buffer = entry_price * (cfg.stop_buffer_perc / 100.0)

                orh = float(day_df["orh"].iloc[0])
                orl = float(day_df["orl"].iloc[0])

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
                out.loc[ts, "setup_armed"] = True
                out.loc[ts, "entry_signal"] = True
                out.loc[ts, "side"] = side
                out.loc[ts, "planned_entry_price"] = entry_price
                out.loc[ts, "planned_stop_price"] = stop_price
                out.loc[ts, "risk_per_share"] = risk
                out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND|CONTINUATION"
                out.loc[ts, "signal_type"] = "CONTINUATION"

                trades_today += 1
                break  # take first continuation

        # 2) PULLBACK entry (optional secondary)
        if cfg.allow_pullback_entry and trades_today < cfg.max_trades_per_day:
            arm_end = min(len(scan), cfg.pullback_arm_window_bars + 1)
            arm = scan.iloc[:arm_end]

            touched = False
            for ts, r in arm.iterrows():
                if trades_today >= cfg.max_trades_per_day:
                    break
                if not filters_ok(ts):
                    continue

                dist = float(r["dist_from_ema_perc"])
                pvs = str(r["price_vs_ema"])

                out.loc[ts, "trend_ok"] = True
                out.loc[ts, "setup_armed"] = True

                if breakout_dir == "UP":
                    if (dist <= cfg.pullback_touch_band_perc) and (pvs in {"AT","BELOW"}):
                        touched = True
                    if touched and pvs == "ABOVE":
                        pos = day_df.index.get_indexer([ts])[0]
                        if pos + 1 >= len(day_df):
                            break
                        entry_ts = day_df.index[pos + 1]
                        entry_price = float(day_df.loc[entry_ts]["open"])
                        buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                        stop_price = float(day_df["orh"].iloc[0]) - buffer
                        risk = entry_price - stop_price
                        if risk <= 0:
                            continue

                        out.loc[ts, "entry_signal"] = True
                        out.loc[ts, "side"] = "LONG"
                        out.loc[ts, "planned_entry_price"] = entry_price
                        out.loc[ts, "planned_stop_price"] = stop_price
                        out.loc[ts, "risk_per_share"] = risk
                        out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND|PULLBACK"
                        out.loc[ts, "signal_type"] = "PULLBACK"
                        trades_today += 1
                        break
                else:
                    if (dist >= -cfg.pullback_touch_band_perc) and (pvs in {"AT","ABOVE"}):
                        touched = True
                    if touched and pvs == "BELOW":
                        pos = day_df.index.get_indexer([ts])[0]
                        if pos + 1 >= len(day_df):
                            break
                        entry_ts = day_df.index[pos + 1]
                        entry_price = float(day_df.loc[entry_ts]["open"])
                        buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                        stop_price = float(day_df["orl"].iloc[0]) + buffer
                        risk = stop_price - entry_price
                        if risk <= 0:
                            continue

                        out.loc[ts, "entry_signal"] = True
                        out.loc[ts, "side"] = "SHORT"
                        out.loc[ts, "planned_entry_price"] = entry_price
                        out.loc[ts, "planned_stop_price"] = stop_price
                        out.loc[ts, "risk_per_share"] = risk
                        out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND|PULLBACK"
                        out.loc[ts, "signal_type"] = "PULLBACK"
                        trades_today += 1
                        break

    return out


def main():
    cfg = MomentumConfig()

    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb_signals_momentum.csv")

    print("\n[Module D-M] Loading ORB+EMA CSV:")
    print(f"  {in_path}")
    df = load_input(in_path, cfg.timezone)

    print("Computing momentum signals...")
    out = compute_signals(df, cfg)

    n = int(out["entry_signal"].sum())
    print(f"\n✅ Entry signals found (momentum): {n}")

    sigs = out[out["entry_signal"]].copy()
    if not sigs.empty:
        cols = ["session_date","signal_type","side","planned_entry_price","planned_stop_price","risk_per_share","signal_reason"]
        print("\nFirst 10 signals:")
        print(sigs[cols].head(10).round(6))

    out.to_csv(out_path, index=True)
    print(f"\nSaved momentum signals CSV to: {out_path}")
    print("\nNext: run Module E-M (execution) using EMA confirm=1 for momentum exits.\n")


if __name__ == "__main__":
    main()
