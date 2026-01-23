#!/usr/bin/env python3
"""daily_runner.py

Production orchestrator for EMA20 Scanner.

This runner is intentionally *thin*: it calls the existing step scripts as subprocesses,
and only adds scheduling/time-window logic (America/Chicago) so you don't have to flip
config flags manually.

Modes
-----
- morning_prep:
    Step1 (TradingView universe) -> Step2 (YF daily -> SQLite) -> Step3 (scan as-of last trading day)
    Step3 is run with env overrides: EMA_TEST_MODE=1, EMA_ASOF_DATE=<last trading day>,
    EMA_DISCORD_ENABLED=0 (disabled for prep).
- live:
    Starts the live monitoring loop (run_live_tracker_yf.py). If started before the selected
    session begins, it will wait.
- eod:
    Step2 -> Step3 (final EOD outputs + Discord summary). Step3 is run with EMA_TEST_MODE=0.
- daily:
    Full-day orchestrator:
      * If before session open: runs morning_prep, waits for open, runs live until close, then runs eod.
      * If during session: ensures morning_prep artifacts exist (runs prep if missing), then runs live until close, then runs eod.
      * If after close: runs eod only.

Usage
-----
  python daily_runner.py --mode morning_prep
  python daily_runner.py --mode live
  python daily_runner.py --mode eod
  python daily_runner.py --mode daily

Optional flags
--------------
  --skip_tv       Skip TradingView universe download in morning_prep/daily.
  --session       RTH | PRE | POST | ALL  (passed to live via EMA_LIVE_SESSION_MODE env override)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import pandas as pd
import exchange_calendars as ecals

CHI_TZ = ZoneInfo("America/Chicago")
CAL = ecals.get_calendar("XNYS")


def _run(cmd: list[str], env: dict | None = None) -> None:
    """Run a subprocess and stream output. Raises on non-zero exit."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update({k: str(v) for k, v in env.items() if v is not None})
    print(f"\n[RUN] {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=merged_env)


def _today_str_chi() -> str:
    return datetime.now(CHI_TZ).strftime("%Y-%m-%d")


def _is_trading_day(d: date) -> bool:
    # sessions are tz-naive at midnight; use string slicing to be robust
    ds = d.strftime("%Y-%m-%d")
    if callable(getattr(CAL, "schedule", None)):
        sched = CAL.schedule(start_date=ds, end_date=ds)
    else:
        sched = CAL.schedule.loc[ds:ds]
    return not sched.empty


def _last_trading_day_str(ref: date | None = None) -> str:
    """Return last trading session date (YYYY-MM-DD) strictly before 'ref' (Chicago date)."""
    if ref is None:
        ref = datetime.now(CHI_TZ).date()

    # look back up to 10 calendar days to find a session
    for i in range(1, 15):
        d = ref - timedelta(days=i)
        if _is_trading_day(d):
            return d.strftime("%Y-%m-%d")
    raise RuntimeError("Could not find a recent trading day in the last 14 calendar days.")


def _session_times_chi(session_date_str: str) -> tuple[datetime, datetime]:
    """Get NYSE open/close for the given session date, converted to America/Chicago."""
    if callable(getattr(CAL, "schedule", None)):
        sched = CAL.schedule(start_date=session_date_str, end_date=session_date_str)
    else:
        sched = CAL.schedule.loc[session_date_str:session_date_str]

    if sched.empty:
        raise RuntimeError(f"No NYSE session for {session_date_str} (market closed).")

    row = sched.iloc[0]
    open_ts = row["market_open"] if "market_open" in row else row["open"]
    close_ts = row["market_close"] if "market_close" in row else row["close"]

    open_dt = pd.Timestamp(open_ts).to_pydatetime().astimezone(CHI_TZ)
    close_dt = pd.Timestamp(close_ts).to_pydatetime().astimezone(CHI_TZ)
    return open_dt, close_dt


def _wait_until(target: datetime) -> None:
    while True:
        now = datetime.now(CHI_TZ)
        if now >= target:
            return
        remaining = (target - now).total_seconds()
        sleep_s = min(60, max(1, int(remaining)))
        print(f"[WAIT] {target.strftime('%Y-%m-%d %H:%M:%S %Z')} (in {int(remaining)}s)")
        time.sleep(sleep_s)


def _cross_file_path_for(asof_date: str) -> str:
    # keep logic here to avoid importing config (runner should be thin)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "symbols", f"ema20_cross_{asof_date}.csv")


def _morning_prep(py: str, skip_tv: bool) -> str:
    """Run prep and return the ASOF_DATE used."""
    asof = _last_trading_day_str()
    if not skip_tv:
        _run([py, "run_step1_download_tv.py"])
    _run([py, "run_step2_fetch_yf_to_sqlite.py"])
    # Step3 as-of last trading day, discord disabled
    env = {
        "EMA_TEST_MODE": "1",
        "EMA_ASOF_DATE": asof,
        "EMA_TEST_STATE_MODE": "read_only",
        "EMA_DISCORD_ENABLED": "0",
    }
    _run([py, "run_step3_scan_from_sqlite.py"], env=env)
    return asof


def _eod(py: str) -> None:
    _run([py, "run_step2_fetch_yf_to_sqlite.py"])
    env = {
        "EMA_TEST_MODE": "0",
        "EMA_ASOF_DATE": "",
        # allow user config to control discord; but force-enable if they want via config
        "EMA_DISCORD_ENABLED": os.environ.get("EMA_DISCORD_ENABLED", ""),
    }
    _run([py, "run_step3_scan_from_sqlite.py"], env=env)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["morning_prep", "live", "eod", "daily"])
    ap.add_argument("--skip_tv", action="store_true", help="Skip Step1 TradingView download")
    ap.add_argument("--session", default=None, choices=["RTH","PRE","POST","ALL"], help="Override live session mode")
    args = ap.parse_args()

    py = sys.executable

    if args.mode == "morning_prep":
        asof = _morning_prep(py, args.skip_tv)
        print(f"\nMorning prep complete. ASOF_DATE={asof}")
        print("Start live with: python daily_runner.py --mode live")
        return

    if args.mode == "eod":
        _eod(py)
        print("\nEOD run complete.")
        return

    if args.mode == "live":
        # If user wants to override session mode without editing config
        live_env = {}
        if args.session:
            live_env["EMA_LIVE_SESSION_MODE"] = args.session
        _run([py, "run_live_tracker_yf.py"], env=live_env)
        return

    # daily mode
    today = datetime.now(CHI_TZ).date()
    today_str = today.strftime("%Y-%m-%d")

    # Determine today's session open/close (if market open today)
    if not _is_trading_day(today):
        print(f"Today ({today_str}) is not a trading day. Running EOD-style scan only.")
        _eod(py)
        return

    open_dt, close_dt = _session_times_chi(today_str)
    now = datetime.now(CHI_TZ)

    if now < open_dt:
        # Pre-open: run morning prep then wait then live then eod
        asof = _morning_prep(py, args.skip_tv)
        cross_path = _cross_file_path_for(asof)
        print(f"\nPrep done. Cross universe: {cross_path}")
        _wait_until(open_dt)
        live_env = {}
        if args.session:
            live_env["EMA_LIVE_SESSION_MODE"] = args.session
        _run([py, "run_live_tracker_yf.py"], env=live_env)
        # Live script typically runs until close; if it returns early, wait until close
        if datetime.now(CHI_TZ) < close_dt:
            _wait_until(close_dt + timedelta(minutes=2))
        _eod(py)
        return

    if open_dt <= now <= close_dt:
        # During session: ensure cross universe exists; if missing, run prep (skip TV by default if user passed)
        asof = _last_trading_day_str(today)
        cross_path = _cross_file_path_for(asof)
        if not os.path.exists(cross_path):
            print(f"Cross universe not found ({cross_path}). Running morning prep (no Discord).")
            _morning_prep(py, args.skip_tv)
        live_env = {}
        if args.session:
            live_env["EMA_LIVE_SESSION_MODE"] = args.session
        _run([py, "run_live_tracker_yf.py"], env=live_env)
        if datetime.now(CHI_TZ) < close_dt:
            _wait_until(close_dt + timedelta(minutes=2))
        _eod(py)
        return

    # After close
    print("After market close. Running EOD finalization.")
    _eod(py)


if __name__ == "__main__":
    main()
