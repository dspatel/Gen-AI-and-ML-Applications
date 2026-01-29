#!/usr/bin/env python3
"""
Multi-window backtest module (EOD, daily bars only).

Purpose
- Run the EMA20-cross + anchored-window breakout logic over a historical date range
- Compare results across multiple PRIMARY / SECONDARY window lengths without touching your production SQLite state.

Outputs
- data/backtests/backtest_summary_<timestamp>.csv
- data/backtests/backtest_alerts_<timestamp>.csv

Usage example
  python tools/multi_window_backtest.py --db data/cache/marketdata.sqlite \
    --start 2025-10-01 --end 2026-01-15 \
    --primary 20,30,35 --secondary 21 --rearm_on_reentry 1
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from zoneinfo import ZoneInfo

import pandas as pd

from utils.sqlite_store import connect_db, read_daily_bars
from utils.indicators import find_latest_range_cross


@dataclass
class SymState:
    last_cross_date: str | None = None
    last_cross_dir: str | None = None
    window_days_primary: int | None = None
    window_high_primary: float | None = None
    window_low_primary: float | None = None
    window_days_secondary: int | None = None
    window_high_secondary: float | None = None
    window_low_secondary: float | None = None
    armed: int = 1


def compute_anchored_window_before_cross(df: pd.DataFrame, cross_date: str, window_days: int):
    """
    Compute (high, low) over the N trading days immediately BEFORE cross_date.
    The cross_date day itself is excluded.
    df must have columns: Date, High, Low
    """
    if not cross_date:
        return None
    df = df.sort_values("Date").reset_index(drop=True)
    cross_dt = pd.to_datetime(cross_date).date()
    ix = df.index[df["Date"].dt.date == cross_dt]
    if len(ix) == 0:
        return None
    i = int(ix[0])
    start = max(0, i - window_days)
    sub = df.iloc[start:i]
    if len(sub) < window_days:
        return None
    return float(sub["High"].max()), float(sub["Low"].min())


def is_reentry(close: float, window_low: float, window_high: float) -> bool:
    return (close >= window_low) and (close <= window_high)


def parse_int_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/cache/marketdata.sqlite")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--primary", default="35", help="comma list, e.g. 20,30,35")
    ap.add_argument("--secondary", default="21", help="comma list, e.g. 14,21,35 (use 0 to disable)")
    ap.add_argument("--cross_lookback", type=int, default=30)
    ap.add_argument("--rearm_on_reentry", type=int, default=1)
    ap.add_argument("--allow_alert_on_cross_date", type=int, default=0)
    args = ap.parse_args()

    primary_list = parse_int_list(args.primary)
    secondary_list = parse_int_list(args.secondary)
    # If user passes 0, treat as disabled.
    secondary_list = [x for x in secondary_list if x > 0]

    start_dt = pd.to_datetime(args.start).date()
    end_dt = pd.to_datetime(args.end).date()
    if end_dt < start_dt:
        raise SystemExit("--end must be >= --start")

    conn = connect_db(args.db)

    # Build symbol universe from DB (all symbols present in daily_bars)
    sym_df = pd.read_sql_query("SELECT DISTINCT symbol FROM daily_bars", conn)
    symbols = sorted(sym_df["symbol"].tolist())

    all_alert_rows = []
    summary_rows = []

    for d1, d2 in product(primary_list, (secondary_list or [None])):
        cfg_name = f"P{d1}_S{d2 if d2 is not None else 'OFF'}"
        states: dict[str, SymState] = {s: SymState() for s in symbols}

        alerts = 0
        longs = 0
        shorts = 0

        for sym in symbols:
            df = read_daily_bars(conn, sym, limit_rows=1000)
            if df is None or df.empty:
                continue
            df = df.sort_values("Date").reset_index(drop=True)
            # Filter date range upfront for iteration
            df["d"] = df["Date"].dt.date
            df = df[(df["d"] >= start_dt) & (df["d"] <= end_dt)]
            if df.empty:
                continue

            st = states[sym]

            # Iterate day by day
            for i in range(len(df)):
                today = df.iloc[i]
                today_date = today["d"].isoformat()
                close = float(today["Close"])
                ema20 = float(today["EMA20"])
                ema20_h = float(today.get("EMA20_H", float("nan")))
                ema20_l = float(today.get("EMA20_L", float("nan")))

                # Find latest cross as-of today using history up to i
                hist = df.iloc[: i + 1].copy()
                cross = find_latest_range_cross(hist, ema_col="EMA20", lookback_days=args.cross_lookback)
                if cross is None:
                    continue

                latest_cross_date = cross["cross_date"]
                latest_cross_dir = cross["direction"]

                # Refresh state if new cross OR window-length changed OR windows missing
                need_refresh = (
                    st.last_cross_date != latest_cross_date
                    or st.window_days_primary != d1
                    or (d2 is None and st.window_days_secondary is not None)
                    or (d2 is not None and st.window_days_secondary != d2)
                    or st.window_high_primary is None
                    or st.window_low_primary is None
                    or (d2 is not None and (st.window_high_secondary is None or st.window_low_secondary is None))
                )

                if need_refresh:
                    win1 = compute_anchored_window_before_cross(hist, latest_cross_date, d1)
                    if win1 is None:
                        continue
                    st.window_days_primary = d1
                    st.window_high_primary, st.window_low_primary = win1

                    if d2 is not None:
                        win2 = compute_anchored_window_before_cross(hist, latest_cross_date, d2)
                        if win2 is None:
                            continue
                        st.window_days_secondary = d2
                        st.window_high_secondary, st.window_low_secondary = win2
                    else:
                        st.window_days_secondary = None
                        st.window_high_secondary = None
                        st.window_low_secondary = None

                    st.last_cross_date = latest_cross_date
                    st.last_cross_dir = latest_cross_dir
                    st.armed = 1

                # Rearm logic
                if args.rearm_on_reentry and st.armed == 0 and is_reentry(close, st.window_low_primary, st.window_high_primary):
                    st.armed = 1

                # Cross-date toggle
                cross_dt = pd.to_datetime(st.last_cross_date).date()
                today_dt = pd.to_datetime(today_date).date()
                allowed = (today_dt >= cross_dt) if args.allow_alert_on_cross_date else (today_dt > cross_dt)

                long_candidate = bool(allowed and st.armed == 1 and close > st.window_high_primary and close > ema20)
                short_candidate = bool(allowed and st.armed == 1 and close < st.window_low_primary and close < ema20)

                if not (long_candidate or short_candidate):
                    continue

                signal = "LONG" if long_candidate else "SHORT"
                alerts += 1
                longs += int(signal == "LONG")
                shorts += int(signal == "SHORT")

                rng = max(st.window_high_primary - st.window_low_primary, 1e-9)
                break_dist = (close - st.window_high_primary) if signal == "LONG" else (st.window_low_primary - close)
                break_pct = break_dist / rng

                all_alert_rows.append({
                    "Config": cfg_name,
                    "Symbol": sym,
                    "EventDate": today_date,
                    "Signal": signal,
                    "CrossDate": st.last_cross_date,
                    "CrossDirection": st.last_cross_dir,
                    "PrimaryWindowDaysUsed": d1,
                    "WindowHigh_primary": st.window_high_primary,
                    "WindowLow_primary": st.window_low_primary,
                    "BreakPct_primary": break_pct,
                    "EMA20": ema20,
                    "EMA20_H": ema20_h,
                    "EMA20_L": ema20_l,
                })

                # Disarm after alert
                st.armed = 0

        summary_rows.append({
            "Config": cfg_name,
            "PrimaryWindowDaysUsed": d1,
            "SecondaryWindowDaysUsed": (d2 if d2 is not None else None),
            "Start": args.start,
            "End": args.end,
            "CrossLookbackDays": args.cross_lookback,
            "RearmOnReentry": bool(args.rearm_on_reentry),
            "AllowAlertOnCrossDate": bool(args.allow_alert_on_cross_date),
            "Alerts": alerts,
            "Longs": longs,
            "Shorts": shorts,
        })

    out_dir = "data/backtests"
    import os
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(tz=ZoneInfo("America/Chicago")).strftime("%Y%m%d_%H%M%S")

    alerts_df = pd.DataFrame(all_alert_rows).sort_values(["Config","EventDate","Symbol"])
    summary_df = pd.DataFrame(summary_rows).sort_values(["Alerts"], ascending=False)

    alerts_path = f"{out_dir}/backtest_alerts_{ts}.csv"
    summary_path = f"{out_dir}/backtest_summary_{ts}.csv"

    alerts_df.to_csv(alerts_path, index=False)
    summary_df.to_csv(summary_path, index=False)

    print(f"Wrote: {summary_path}")
    print(f"Wrote: {alerts_path}")


if __name__ == "__main__":
    main()
