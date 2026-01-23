# ============================================================
# Module C: ORB Engine (Opening Range Breakout)
# OR window: first 30 minutes (09:30–10:00 ET) on 5-minute bars
#
# Input:
#   data/SPY_30d_5m_yahoo_ema20.csv
#
# Output:
#   data/SPY_30d_5m_yahoo_ema20_orb.csv
#
# Adds columns:
#   - session_date
#   - orb_locked (bool)
#   - orh, orl (Opening Range High/Low)
#   - orb_breakout (UP/DOWN/NONE)  [event on the first breakout bar after lock]
#   - orb_breakout_time (timestamp of first breakout, repeated for all rows that day)
#
# Run:
#   python test_orb_spy.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ORBConfig:
    timezone: str = "America/New_York"
    market_open: str = "09:30"
    orb_end: str = "10:00"            # 30-min OR window end (exclusive in between_time)
    market_close: str = "16:00"
    confirm_closes: int = 1           # for now: 1 close beyond ORH/ORL confirms breakout
    regular_hours_only: bool = True


def load_input(path: str, tz: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input CSV not found: {path}")

    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError("CSV must contain a 'timestamp' column.")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["timestamp"] = df["timestamp"].dt.tz_convert(tz)
    df = df.set_index("timestamp").sort_index()

    for c in ["open", "high", "low", "close"]:
        if c not in df.columns:
            raise ValueError(f"Missing required column: {c}")

    return df


def filter_regular_hours(df: pd.DataFrame, cfg: ORBConfig) -> pd.DataFrame:
    if not cfg.regular_hours_only:
        return df

    out = df.copy()
    out = out[out.index.dayofweek < 5]
    out = out.between_time(cfg.market_open, cfg.market_close, inclusive="left")
    return out


def compute_orb(df: pd.DataFrame, cfg: ORBConfig) -> pd.DataFrame:
    out = df.copy()
    out = filter_regular_hours(out, cfg)

    # Session key (date in exchange timezone)
    out["session_date"] = out.index.date.astype(str)

    # Initialize columns
    out["orb_locked"] = False
    out["orh"] = pd.NA
    out["orl"] = pd.NA
    out["orb_breakout"] = "NONE"
    out["orb_breakout_time"] = pd.NaT

    # Process per session
    for session_date, day_df in out.groupby("session_date", sort=True):
        # Opening range window: 09:30 <= t < 10:00
        or_window = day_df.between_time(cfg.market_open, cfg.orb_end, inclusive="left")
        if or_window.empty:
            continue

        orh = float(or_window["high"].max())
        orl = float(or_window["low"].min())

        # Bars after OR window where breakouts can occur
        after_or = day_df[day_df.index >= or_window.index.max()]  # includes last OR bar time
        # Better: only bars strictly after 10:00
        after_or = day_df[day_df.index >= pd.Timestamp(f"{session_date} {cfg.orb_end}", tz=cfg.timezone)]

        # Mark ORH/ORL across all rows of that day after OR is established (including OR window itself)
        day_idx = day_df.index
        out.loc[day_idx, "orh"] = orh
        out.loc[day_idx, "orl"] = orl

        # orb_locked true at/after 10:00
        lock_ts = pd.Timestamp(f"{session_date} {cfg.orb_end}", tz=cfg.timezone)
        out.loc[day_idx, "orb_locked"] = day_idx >= lock_ts

        # Find first confirmed breakout (simple v1: 1 close beyond ORH/ORL)
        breakout_time = None
        breakout_dir = "NONE"
        if not after_or.empty:
            if cfg.confirm_closes <= 1:
                up_break = after_or[after_or["close"] > orh]
                dn_break = after_or[after_or["close"] < orl]
                # pick earliest event among up/down
                candidates = []
                if not up_break.empty:
                    candidates.append((up_break.index[0], "UP"))
                if not dn_break.empty:
                    candidates.append((dn_break.index[0], "DOWN"))
                if candidates:
                    breakout_time, breakout_dir = sorted(candidates, key=lambda x: x[0])[0]
            else:
                # Multi-close confirmation (basic implementation)
                closes = after_or["close"]
                up_mask = closes > orh
                dn_mask = closes < orl
                # rolling sum of True values
                up_conf = up_mask.rolling(cfg.confirm_closes).sum() >= cfg.confirm_closes
                dn_conf = dn_mask.rolling(cfg.confirm_closes).sum() >= cfg.confirm_closes
                candidates = []
                if up_conf.any():
                    candidates.append((up_conf[up_conf].index[0], "UP"))
                if dn_conf.any():
                    candidates.append((dn_conf[dn_conf].index[0], "DOWN"))
                if candidates:
                    breakout_time, breakout_dir = sorted(candidates, key=lambda x: x[0])[0]

        # Store breakout event
        if breakout_time is not None:
            # mark the breakout bar with UP/DOWN
            out.loc[breakout_time, "orb_breakout"] = breakout_dir
            # store breakout_time for all bars that day (handy later)
            out.loc[day_idx, "orb_breakout_time"] = breakout_time

    return out


def sanity_checks(df: pd.DataFrame) -> tuple[bool, list[str]]:
    ok = True
    msgs = []

    required = ["session_date", "orb_locked", "orh", "orl", "orb_breakout", "orb_breakout_time"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return False, [f"ERROR: Missing required ORB columns: {missing}"]

    # Basic OR values should exist for most rows
    if df["orh"].isna().mean() > 0.1:
        msgs.append("WARNING: Many rows missing ORH/ORL (check market-hours filtering).")

    # ORH should be >= ORL always when present
    valid = df["orh"].notna() & df["orl"].notna()
    if (df.loc[valid, "orh"] < df.loc[valid, "orl"]).any():
        ok = False
        msgs.append("ERROR: Found ORH < ORL (should never happen).")

    return ok, msgs


def main():
    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb.csv")
    cfg = ORBConfig(confirm_closes=1)

    print("\n[Module C] Loading EMA CSV:")
    print(f"  {in_path}")
    df = load_input(in_path, cfg.timezone)
    df = filter_regular_hours(df, cfg)

    print(f"Loaded rows: {len(df):,}")
    print(f"Start: {df.index.min()} | End: {df.index.max()}")

    print("\nComputing ORB (first 30 minutes: 09:30–10:00 ET)...")
    out = compute_orb(df, cfg)

    ok, msgs = sanity_checks(out)
    for m in msgs:
        print(m)
    if not ok:
        print("\nSanity checks failed. Fix issues before moving on.")
        return

    # Show a sample day: pick most recent session_date
    last_day = out["session_date"].dropna().iloc[-1]
    day_df = out[out["session_date"] == last_day]

    print(f"\nSample day: {last_day}")
    print("ORH/ORL:", float(day_df["orh"].iloc[0]), float(day_df["orl"].iloc[0]))

    # show around lock and possible breakout
    lock_ts = pd.Timestamp(f"{last_day} {cfg.orb_end}", tz=cfg.timezone)
    window = day_df[(day_df.index >= lock_ts - pd.Timedelta(minutes=15)) &
                    (day_df.index <= lock_ts + pd.Timedelta(minutes=60))]
    cols = ["open", "high", "low", "close", "orh", "orl", "orb_locked", "orb_breakout"]
    print("\nBars around OR lock (and after):")
    print(window[cols].head(20).round(4))

    # Save
    out.to_csv(out_path, index=True)
    print(f"\n✅ Saved ORB-enriched CSV to: {out_path}")
    print("\nNext: Module D will combine ORB + EMA for conservative signals.\n")


if __name__ == "__main__":
    main()
