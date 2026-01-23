# ============================================================
# Module D: Conservative Signals + Trade Plan (ORB + EMA20)
#
# Conservative rules (v1):
# - Ignore until ORB breakout is CONFIRMED (orb_breakout_time exists)
# - Entry is PULLBACK ONLY (no breakout-close chase)
# - Long bias if breakout UP; Short bias if breakout DOWN
# - EMA trend filter: ema_slope_perc over lookback >= threshold (or <= -threshold for shorts)
# - Chop filter: count EMA crosses in lookback <= max_crosses
# - Overextension filter: abs(dist_from_ema_perc) <= max_dist
#
# Entry trigger (LONG):
# 1) After breakout time, arm pullback for N bars
# 2) Pullback touch: dist_from_ema_perc <= touch_band AND price_vs_ema in {AT, BELOW}
# 3) Confirmation candle: closes back ABOVE EMA20 (price_vs_ema == ABOVE)
# 4) Planned entry = next bar open
#
# Stop (LONG): swing low over last swing_lookback bars (ending at confirm candle) - buffer
# Stop (SHORT): swing high over last swing_lookback bars + buffer
#
# Output:
#   data/SPY_30d_5m_yahoo_ema20_orb_signals_conservative.csv
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class ConservativeConfig:
    timezone: str = "America/New_York"

    # Filters
    ema_slope_lookback_bars: int = 5
    ema_slope_min_perc: float = 0.02      # percent over lookback (e.g., 0.02%)
    chop_lookback_bars: int = 10
    chop_max_ema_crosses: int = 2
    max_dist_from_ema_perc: float = 0.60  # percent

    # Pullback rules
    pullback_arm_window_bars: int = 12     # 12 bars = 60 minutes
    pullback_touch_band_perc: float = 0.10 # within 0.10% of EMA counts as touch

    # Stop rules
    swing_lookback_bars: int = 6
    stop_buffer_perc: float = 0.05        # buffer as % of price (SPY-friendly)

    # Trade limits
    max_trades_per_day: int = 1            # conservative: 1 trade per day total


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
        "open","high","low","close",
        "ema20","ema_slope_perc","dist_from_ema_perc","price_vs_ema",
        "session_date","orh","orl","orb_locked","orb_breakout","orb_breakout_time"
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    return df


def ema_cross_count(price_vs_ema_series: pd.Series) -> int:
    """
    Counts transitions across ABOVE/BELOW. 'AT' is treated as neutral.
    We count a cross when state changes between ABOVE and BELOW.
    """
    s = price_vs_ema_series.replace({"AT": pd.NA}).ffill()
    s = s[s.notna()]
    if len(s) < 2:
        return 0
    crosses = (s != s.shift(1)).sum() - 1
    return int(max(crosses, 0))


def compute_signals(df: pd.DataFrame, cfg: ConservativeConfig) -> pd.DataFrame:
    out = df.copy()

    # Add output columns
    out["chop_ema_crosses"] = pd.NA
    out["trend_ok"] = False
    out["setup_armed"] = False
    out["pullback_touched"] = False
    out["entry_signal"] = False

    out["planned_entry_price"] = pd.NA
    out["planned_stop_price"] = pd.NA
    out["risk_per_share"] = pd.NA
    out["side"] = pd.NA
    out["signal_reason"] = pd.NA

    # Per day processing
    for session_date, day_df in out.groupby("session_date", sort=True):
        # Identify confirmed breakout time (from Module C)
        btime = day_df["orb_breakout_time"].dropna()
        if btime.empty:
            continue
        breakout_time = pd.Timestamp(btime.iloc[0])  # already tz-aware

        # Determine direction from the breakout event row
        # Find the row where orb_breakout is UP/DOWN; fallback to first non-NONE
        bdir_rows = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
        if bdir_rows.empty:
            continue
        breakout_dir = bdir_rows["orb_breakout"].iloc[0]  # UP or DOWN

        # Conservative: only 1 trade per day (we'll mark first signal and ignore rest)
        trades_today = 0

        # Iterate bars after breakout_time (inclusive)
        post = day_df[day_df.index >= breakout_time].copy()
        if post.empty:
            continue

        # We will arm the pullback for a fixed number of bars after breakout
        arm_end_idx = min(len(post), cfg.pullback_arm_window_bars + 1)
        arm_window = post.iloc[:arm_end_idx]

        # Precompute chop cross count per bar (rolling window)
        # We compute on the fly: last chop_lookback bars within the day
        for i, (ts, row) in enumerate(day_df.iterrows()):
            look = day_df.loc[:ts].tail(cfg.chop_lookback_bars)["price_vs_ema"]
            crosses = ema_cross_count(look)
            out.loc[ts, "chop_ema_crosses"] = crosses

        # Helper: determine if trend filter passes at a timestamp
        def trend_filter_ok(ts: pd.Timestamp) -> bool:
            r = day_df.loc[ts]
            slope = float(r["ema_slope_perc"])
            if breakout_dir == "UP":
                return slope >= cfg.ema_slope_min_perc
            else:
                return slope <= -cfg.ema_slope_min_perc

        # Helper: overextension filter
        def overextension_ok(ts: pd.Timestamp) -> bool:
            d = abs(float(day_df.loc[ts]["dist_from_ema_perc"]))
            return d <= cfg.max_dist_from_ema_perc

        # Arm logic: only consider bars in arm_window, only if locked and after breakout
        armed = False
        touched = False
        touch_ts = None

        for j, (ts, r) in enumerate(arm_window.iterrows()):
            if trades_today >= cfg.max_trades_per_day:
                break

            # Need ORB locked (post 10:00) and after breakout time
            if not bool(r["orb_locked"]):
                continue

            # Chop filter
            crosses = int(out.loc[ts, "chop_ema_crosses"]) if pd.notna(out.loc[ts, "chop_ema_crosses"]) else 0
            if crosses > cfg.chop_max_ema_crosses:
                continue

            # Trend + overextension filters
            if not trend_filter_ok(ts):
                continue
            if not overextension_ok(ts):
                continue

            # Mark trend ok
            out.loc[ts, "trend_ok"] = True

            # Arm the setup once filters pass (first time)
            if not armed:
                armed = True
            out.loc[ts, "setup_armed"] = armed

            # Now wait for pullback touch (near EMA in direction-specific way)
            dist = float(r["dist_from_ema_perc"])
            pvs = str(r["price_vs_ema"])

            if breakout_dir == "UP":
                # Touch if close is near/under EMA
                if (dist <= cfg.pullback_touch_band_perc) and (pvs in {"AT", "BELOW"}):
                    touched = True
                    touch_ts = ts
                out.loc[ts, "pullback_touched"] = touched

                # Confirmation candle: closes back ABOVE EMA
                if touched and pvs == "ABOVE":
                    # Plan entry at next bar open
                    day_positions = day_df.index.get_indexer([ts])[0]
                    if day_positions + 1 >= len(day_df):
                        break
                    entry_ts = day_df.index[day_positions + 1]
                    entry_price = float(day_df.loc[entry_ts]["open"])

                    # Stop: swing low over last swing_lookback bars ending at confirm candle
                    swing_slice = day_df.loc[:ts].tail(cfg.swing_lookback_bars)
                    swing_low = float(swing_slice["low"].min())
                    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                    stop_price = swing_low - buffer

                    risk = entry_price - stop_price
                    if risk <= 0:
                        continue

                    # Emit signal
                    out.loc[ts, "entry_signal"] = True
                    out.loc[ts, "side"] = "LONG"
                    out.loc[ts, "planned_entry_price"] = entry_price
                    out.loc[ts, "planned_stop_price"] = stop_price
                    out.loc[ts, "risk_per_share"] = risk
                    out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND_UP|PULLBACK_CONFIRM"

                    trades_today += 1
                    break

            else:  # DOWN breakout
                # Touch if close is near/above EMA (pullback rally)
                if (dist >= -cfg.pullback_touch_band_perc) and (pvs in {"AT", "ABOVE"}):
                    touched = True
                    touch_ts = ts
                out.loc[ts, "pullback_touched"] = touched

                # Confirmation candle: closes back BELOW EMA
                if touched and pvs == "BELOW":
                    day_positions = day_df.index.get_indexer([ts])[0]
                    if day_positions + 1 >= len(day_df):
                        break
                    entry_ts = day_df.index[day_positions + 1]
                    entry_price = float(day_df.loc[entry_ts]["open"])

                    # Stop: swing high
                    swing_slice = day_df.loc[:ts].tail(cfg.swing_lookback_bars)
                    swing_high = float(swing_slice["high"].max())
                    buffer = entry_price * (cfg.stop_buffer_perc / 100.0)
                    stop_price = swing_high + buffer

                    risk = stop_price - entry_price
                    if risk <= 0:
                        continue

                    out.loc[ts, "entry_signal"] = True
                    out.loc[ts, "side"] = "SHORT"
                    out.loc[ts, "planned_entry_price"] = entry_price
                    out.loc[ts, "planned_stop_price"] = stop_price
                    out.loc[ts, "risk_per_share"] = risk
                    out.loc[ts, "signal_reason"] = "ORB_CONFIRMED|EMA_TREND_DOWN|PULLBACK_CONFIRM"

                    trades_today += 1
                    break

    return out


def main():
    cfg = ConservativeConfig()

    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb_signals_conservative.csv")

    print("\n[Module D] Loading ORB+EMA CSV:")
    print(f"  {in_path}")
    df = load_input(in_path, cfg.timezone)
    print(f"Loaded rows: {len(df):,} | sessions: {df['session_date'].nunique()}")

    print("\nComputing conservative pullback-only signals + trade plans...")
    out = compute_signals(df, cfg)

    # Count signals
    n_signals = int(out["entry_signal"].sum())
    print(f"\n✅ Entry signals found: {n_signals}")

    # Show first few signals
    sigs = out[out["entry_signal"]].copy()
    if not sigs.empty:
        print("\nFirst 5 signals:")
        cols = [
            "session_date","side","planned_entry_price","planned_stop_price","risk_per_share",
            "orh","orl","ema20","ema_slope_perc","dist_from_ema_perc","chop_ema_crosses","signal_reason"
        ]
        print(sigs[cols].head(5).round(6))

        # Show context around first signal day
        first_day = sigs["session_date"].iloc[0]
        first_ts = sigs.index[0]
        day_df = out[out["session_date"] == first_day]
        start = first_ts - pd.Timedelta(minutes=30)
        end = first_ts + pd.Timedelta(minutes=60)
        ctx = day_df[(day_df.index >= start) & (day_df.index <= end)]

        print(f"\nContext around first signal ({first_day} @ {first_ts}):")
        ctx_cols = [
            "open","high","low","close","ema20","price_vs_ema","dist_from_ema_perc",
            "orh","orl","orb_locked","orb_breakout","setup_armed","pullback_touched","entry_signal"
        ]
        print(ctx[ctx_cols].round(4).head(30))
    else:
        print("\nNo signals found. This can happen depending on filters; we can loosen thresholds after review.")

    out.to_csv(out_path, index=True)
    print(f"\nSaved signals CSV to: {out_path}")
    print("\nNext: Module E (trade execution) will convert trade plans into entries/exits/stops.\n")


if __name__ == "__main__":
    main()
