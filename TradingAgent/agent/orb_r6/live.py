"""Live runner.

Key points:
  - Session/timezone logic is America/Chicago (CT).
  - We ingest/process only *completed* candles to avoid Yahoo "Data doesn't exist" errors
    that happen when requesting not-yet-available intraday bars.
"""

BUILD_VERSION = "0.10.9"

import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Optional, List

import pandas as pd
import pandas_market_calendars as mcal

from .config_loader import load_config
from .db import connect, init_db
from .prepare_asof import ensure_asof_ready
from .symbols import load_symbols
from .breakouts import ensure_breakout_tables, load_rr_rows, load_broken_horizons, HorizonState
from .notifier import load_templates
from .time_utils import combine_cst_date_time
from .ingest_yf import run_ingest
from .breakout_engine import evaluate_bar_close_only

CST = ZoneInfo("America/Chicago")


def _is_session_day(calendar_name: str, cst_date: str) -> bool:
    cal = mcal.get_calendar(calendar_name)
    sched = cal.schedule(start_date=cst_date, end_date=cst_date)
    return not sched.empty


def _interval_minutes(interval: str) -> int:
    s = interval.strip().lower()
    if s.endswith("m"):
        return int(s[:-1])
    raise ValueError(f"Unsupported interval: {interval} (expected like '15m')")


def _last_complete_close_ts(now_cst: datetime, interval: str, session_start_dt: datetime) -> Optional[pd.Timestamp]:
    """Last *completed* candle close timestamp (CST) for the interval.

    For intraday bars, the first completed close occurs at session_start + interval.
    Before that, return None so we don't ask Yahoo for non-existent candles (which can
    trigger 'Data doesn\'t exist for startDate/endDate').
    """
    mins = _interval_minutes(interval)

    # Safety buffer: treat 'now-1min' as safe to avoid partially forming last candle
    safe_now = pd.Timestamp(now_cst) - pd.Timedelta(minutes=1)
    anchor = pd.Timestamp(session_start_dt)

    first_close = anchor + pd.Timedelta(minutes=mins)
    if safe_now < first_close:
        return None

    n = int(((safe_now - anchor).total_seconds()) // (mins * 60))
    return anchor + pd.Timedelta(minutes=n * mins)


def _load_new_candles(conn: sqlite3.Connection, symbol: str, interval: str, cst_date: str, after_close_ts: Optional[str]) -> pd.DataFrame:
    q = """
    SELECT open_ts_cst, close_ts_cst, open, high, low, close
    FROM candles
    WHERE symbol=? AND interval=? AND cst_date=?
    {after_clause}
    ORDER BY open_ts_cst
    """
    after_clause = "" if not after_close_ts else "AND close_ts_cst > ?"
    q = q.format(after_clause=after_clause)
    params: List[str] = [symbol, interval, cst_date]
    if after_close_ts:
        params.append(after_close_ts)
    return pd.read_sql_query(q, conn, params=params)


def run(config_path: str = "orb_r6_config.yaml") -> None:
    cfg = load_config(config_path)
    init_db_path = cfg.db_path

    # Live always runs on the CURRENT session. If user set asof_date_cst, that's for replay/backtests.
    if cfg.asof_date_cst:
        print(f"[WARN] config.asof_date_cst is set to {cfg.asof_date_cst}. Live ignores this and uses today.")
    now_cst = datetime.now(CST)
    session_date_cst = now_cst.date().isoformat()

    if not _is_session_day(cfg.session.calendar, session_date_cst):
        print(f"[LIVE] {session_date_cst} is not a trading session day ({cfg.session.calendar}). Exiting.")
        return

    symbols = load_symbols(cfg.symbols)
    interval = cfg.market_data.interval
    or_minutes = cfg.market_data.opening_range_minutes
    horizons = cfg.market_data.lookback_days

    session_start_dt = combine_cst_date_time(session_date_cst, cfg.session.start)
    session_end_dt = combine_cst_date_time(session_date_cst, cfg.session.end)
    or_end_dt = session_start_dt + timedelta(minutes=int(or_minutes))

    # If started before open: wait until the session starts.
    # (We compute the last completed candle inside the main poll loop.)
    if now_cst < session_start_dt:
        print(f"[LIVE] Started before market open. Waiting until {session_start_dt} CT...")
        while datetime.now(CST) < session_start_dt:
            time.sleep(10)
        now_cst = datetime.now(CST)

    conn = connect(cfg.db_path)
    init_db(conn)
    ensure_breakout_tables(conn)

    print("=" * 70)
    print("LIVE: provider polling + DB-first RR + close-only breakout engine (build "+BUILD_VERSION+")")
    print(f"DB: {cfg.db_path}")
    print(f"Calendar: {cfg.session.calendar} | Session: {cfg.session.start}-{cfg.session.end} CT | OR: {or_minutes}m")
    print(f"Interval: {interval} | Horizons: {horizons}")
    print(f"Provider: {cfg.market_data.provider} (alpaca_feed={cfg.market_data.alpaca_feed})")
    print(f"Symbols: {symbols}")
    print("=" * 70)

    discord_cfg = cfg.discord or {}
    templates = load_templates(discord_cfg.get('templates_path', './templates/discord_alerts.yaml'))
    discord_enabled = bool(discord_cfg.get('enabled', False))
    webhook = (discord_cfg.get('webhook_url') or '')
    tag = ""  # live tag optional (replay uses [REPLAY])

    # Ensure DB has everything we need for today session (prior sessions + OR table + RR rows).
    # This is DB-first: fetch candles if missing, compute ORs, compute RR rows (both variants).
    ensure_asof_ready(conn, cfg, session_date_cst, alpaca_env_prefix="R6")

    # Load RR rows (complete-only). Pre-OR should usually be complete. Post-OR may become complete after OR is computed.
    rr_pre_by_sym: Dict[str, Dict[int, object]] = {}
    rr_post_by_sym: Dict[str, Dict[int, object]] = {}
    rr_seed_by_sym: Dict[str, Dict[int, object]] = {}

    for sym in symbols:
        rr_pre_by_sym[sym] = load_rr_rows(conn, sym, session_date_cst, or_minutes, interval, include_today_or=0)
        rr_post_by_sym[sym] = load_rr_rows(conn, sym, session_date_cst, or_minutes, interval, include_today_or=1)
        rr_seed_by_sym[sym] = rr_pre_by_sym[sym] or rr_post_by_sym[sym]

    state_by_sym_phase: Dict[str, Dict[int, Dict[int, HorizonState]]] = {sym: {0: {}, 1: {}} for sym in symbols}
    or_end_ts = pd.Timestamp(or_end_dt)

    # Restore horizon armed-state per phase to avoid duplicate alerts after restart.
    for sym in symbols:
        for phase in (0, 1):
            broken = load_broken_horizons(conn, sym, session_date_cst, interval, or_minutes, phase)
            for h in broken.keys():
                state_by_sym_phase[sym][phase][int(h)] = HorizonState(armed=False)

    last_close_by_sym: Dict[str, Optional[str]] = {sym: None for sym in symbols}

    refreshed_post_rr = False
    last_heartbeat = time.time()

    # Poll loop
    poll_seconds = 10
    heartbeat_seconds = 60

    while True:
        now_cst = datetime.now(CST)
        if now_cst > session_end_dt:
            print(f"[LIVE] Reached session end ({cfg.session.end} CT). Exiting.")
            break

        # Compute last completed candle close for this interval.
        # Before the first completed close, we should not call Yahoo.
        last_complete = _last_complete_close_ts(now_cst, interval, session_start_dt)
        if last_complete is None:
            # Too early (no completed candles yet). Just wait for the first close.
            time.sleep(poll_seconds)
            continue

        # Ingest up to the last completed candle. The yfinance client may add a tiny
        # inclusive padding internally; we keep the end here at the completed close.
        ingest_end = last_complete.to_pydatetime()

        # Heartbeat
        if time.time() - last_heartbeat >= heartbeat_seconds:
            lc = {k: (v[-19:] if v else None) for k, v in last_close_by_sym.items()}
            phase = "pre-OR" if now_cst < or_end_dt else "post-OR"
            print(f"[HEARTBEAT] {now_cst.isoformat(timespec='seconds')} CT | phase={phase} | last_close={lc}")
            last_heartbeat = time.time()

        # After OR end, refresh RR_post once (because it may have been incomplete earlier).
        if (not refreshed_post_rr) and (now_cst >= or_end_dt):
            ensure_asof_ready(conn, cfg, session_date_cst, alpaca_env_prefix="R6")
            for sym in symbols:
                rr_post_by_sym[sym] = load_rr_rows(conn, sym, session_date_cst, or_minutes, interval, include_today_or=1)
                rr_seed_by_sym[sym] = rr_seed_by_sym[sym] or rr_post_by_sym[sym]
            refreshed_post_rr = True
            print(f"[LIVE] OR window complete. Refreshed post-OR reference rows.")

        # Incremental ingestion for today only (DB-first upsert)
        for sym in symbols:
            try:
                run_ingest(
                    conn=conn,
                    symbol=sym,
                    interval=interval,
                    start_cst=session_start_dt,
                    end_cst=ingest_end,
                    session_dates_cst=[session_date_cst],
                    session_start=cfg.session.start,
                    session_end=cfg.session.end,
                    provider=cfg.market_data.provider,
                    alpaca_feed=cfg.market_data.alpaca_feed,
                    alpaca_env_prefix="R6",
                )
            except Exception as e:
                print(f"[WARN] ingest failed for {sym}: {e}")

        # Process new completed candles in order
        for sym in symbols:
            df = _load_new_candles(conn, sym, interval, session_date_cst, last_close_by_sym[sym])
            if df.empty:
                continue

            # Only process completed bars
            df["close_dt"] = pd.to_datetime(df["close_ts_cst"])
            df = df[df["close_dt"] <= last_complete]
            if df.empty:
                continue

            rr_pre = rr_pre_by_sym.get(sym, {})
            rr_post = rr_post_by_sym.get(sym, {})
            rr_seed = rr_seed_by_sym.get(sym, {})

            for _, row in df.iterrows():
                res = evaluate_bar_close_only(
                    conn,
                    templates=templates,
                    discord_enabled=discord_enabled,
                    webhook=webhook,
                    tag=tag,
                    mode="LIVE",
                    symbol=sym,
                    asof_date_cst=session_date_cst,
                    interval=interval,
                    or_minutes=or_minutes,
                    horizons=horizons,
                    rr_pre=rr_pre,
                    rr_post=rr_post,
                    rr_seed=rr_seed,
                    state_by_h=state_by_sym_phase[sym][0 if pd.to_datetime(row["close_ts_cst"]) < or_end_ts else 1],
                    row=row,
                    or_end_ts_cst=or_end_ts,
                )
                # advance last processed marker regardless of event
                last_close_by_sym[sym] = str(row["close_ts_cst"])

        time.sleep(poll_seconds)

    conn.close()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
