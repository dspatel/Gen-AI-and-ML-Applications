"""daily_runner.py (PRODUCTION)

Orchestrates the 3-step EMA20 Scanner workflow for real market days:

- Step 1: Build universe symbols file (TradingView exports list)
- Step 2: Fetch / upsert daily bars from yfinance into SQLite
- Live:  Monitor intraday (yfinance) and send live Discord alerts
- Step 3: Run EOD scan from SQLite and post CSVs to Discord

Important: This PRODUCTION runner does **not** support test/replay mode.
Use Scanner_TEST for that.
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as ecals

from config import CFG

TZ = ZoneInfo(getattr(CFG, "TIMEZONE", "America/Chicago"))


@dataclass(frozen=True)
class SessionTimes:
    open_dt: datetime
    close_dt: datetime


def _now() -> datetime:
    return datetime.now(tz=TZ)


def _ymd(d: date) -> str:
    return d.isoformat()


def _calendar():
    # NYSE calendar (covers US equities + holidays)
    return ecals.get_calendar("XNYS")


def is_trading_day(d: date) -> bool:
    cal = _calendar()
    return cal.is_session(d)


def last_trading_day(d: date) -> date:
    """Return the most recent session date <= d."""
    cal = _calendar()
    if cal.is_session(d):
        return d
    prev = cal.previous_session(d)
    return prev.date()


def session_times_chicago(d: date) -> SessionTimes:
    """Return RTH open/close for the given session date in America/Chicago."""
    cal = _calendar()
    if not cal.is_session(d):
        raise ValueError(f"{d} is not a trading day")

    open_utc = cal.session_open(d)
    close_utc = cal.session_close(d)
    return SessionTimes(open_dt=open_utc.tz_convert(TZ).to_pydatetime(), close_dt=close_utc.tz_convert(TZ).to_pydatetime())


def _run_script(script: str, args=None, env_overrides=None) -> None:
    """Run a python script using the current interpreter."""
    cmd = [sys.executable, script]
    if args:
        cmd.extend([str(a) for a in args])
    print(f"\n[RUN] {' '.join(cmd)}")
    env = os.environ.copy()
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items()})
    subprocess.run(cmd, check=True, env=env)


def _symbols_file_for(d: date) -> str:
    return os.path.join(CFG.SYMBOLS_DIR, f"symbols_{_ymd(d)}.csv")


def _ensure_step1(today: date) -> None:
    os.makedirs(CFG.SYMBOLS_DIR, exist_ok=True)
    sym_path = _symbols_file_for(today)
    if os.path.exists(sym_path):
        return
    _run_script("run_step1_download_tv.py")


def _ensure_step2(today: date) -> None:
    # Step2 expects a symbols_YYYY-MM-DD.csv produced by step1.
    _ensure_step1(today)
    _run_script("run_step2_fetch_yf_to_sqlite.py")


def _cross_file_for(d: date) -> str:
    return os.path.join(CFG.SYMBOLS_DIR, f"ema20_cross_{_ymd(d)}.csv")


def _ensure_cross_universe(asof: date) -> None:
    """Ensure the ema20_cross_<asof>.csv exists.

    If missing, we generate it by running Step 3 for that as-of date with Discord
    explicitly disabled, so we don't spam EOD messages during morning/live prep.
    """
    os.makedirs(CFG.SYMBOLS_DIR, exist_ok=True)
    cross_path = _cross_file_for(asof)
    if os.path.exists(cross_path):
        return
    print(f"[WARN] Missing cross universe for {_ymd(asof)}. Generating it before live...")
    _run_script(
        "run_step3_scan_from_sqlite.py",
        args=["--asof", _ymd(asof), "--discord", "off"],
        env_overrides={"EMA_DISCORD_ENABLED": "0"},
    )


def _run_live() -> None:
    if not getattr(CFG, "LIVE_ENABLED", True):
        print("LIVE is disabled in config (LIVE_ENABLED=False). Skipping live tracker.")
        return
    _run_script("run_live_tracker_yf.py")


def _run_eod() -> None:
    # Refresh daily bars once more after close, then do the EOD scan.
    _run_script("run_step2_fetch_yf_to_sqlite.py")
    _run_script("run_step3_scan_from_sqlite.py")


def main() -> None:
    now = _now()
    today = now.date()

    if not is_trading_day(today):
        # Production project only runs on market days.
        print(f"Today ({_ymd(today)}) is not a trading day. Exiting (PRODUCTION).")
        return

    sess = session_times_chicago(today)
    print(f"SESSION (RTH): {sess.open_dt:%Y-%m-%d %H:%M:%S %Z} -> {sess.close_dt:%Y-%m-%d %H:%M:%S %Z}")

    # If you start before open: prep DB, then wait for open, then run live.
    if now < sess.open_dt:
        print("Pre-market: running Step 1 & Step 2 prep, then waiting for open...")
        _ensure_step2(today)
        # Ensure the most recent completed session's cross universe exists (prevents fallback to stale days).
        prev_session = last_trading_day(today - timedelta(days=1))
        _ensure_cross_universe(prev_session)

        while _now() < sess.open_dt:
            time.sleep(5)

        print("Market open: starting live tracker...")
        _run_live()
        print("Live tracker ended. Running EOD finalization...")
        _run_eod()
        return

    # During session: run prep if needed, then live; once live exits, EOD.
    if sess.open_dt <= now <= sess.close_dt:
        print("During session: ensuring Step 1 & Step 2 have run, then starting live tracker...")
        _ensure_step2(today)
        prev_session = last_trading_day(today - timedelta(days=1))
        _ensure_cross_universe(prev_session)
        _run_live()
        print("Live tracker ended. Running EOD finalization...")
        _run_eod()
        return

    # After close: just do EOD.
    if now > sess.close_dt:
        print("After market close: running EOD finalization...")
        # Step2 still requires a symbols file; ensure step1 created it.
        _ensure_step1(today)
        _run_eod()
        return


if __name__ == "__main__":
    main()
