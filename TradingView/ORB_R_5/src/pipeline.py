from __future__ import annotations

from .config_loader import AppConfig
from .db import connect, init_db
from .nyse_calendar import build_session_window
from .symbols import load_symbols
from .time_utils import combine_cst_date_time
from .prepare_asof import ensure_asof_ready


def run_from_config(cfg: AppConfig) -> None:
    """Config-driven 'run' entrypoint.

    This module intentionally stays thin and delegates DB-first preparation to
    prepare_asof.ensure_asof_ready so that RUN / REPLAY / LIVE share identical
    backfill + OR + RR behavior.
    """
    interval = cfg.market_data.interval
    horizons = cfg.market_data.lookback_days
    if not horizons:
        raise ValueError("market_data.lookback_days is empty")
    max_h = max(horizons)

    # Optional canonical as-of anchor (YYYY-MM-DD, CST)
    asof_date_cfg = cfg.asof_date_cst

    # Window selection:
    # - If asof_date_cfg is set: build window ending at that session end time.
    # - Else: build window ending at current session end time (today).
    asof_cst = None
    if asof_date_cfg:
        asof_cst = combine_cst_date_time(asof_date_cfg, cfg.session.end)

    window = build_session_window(
        calendar_name=cfg.session.calendar,
        n=max_h,
        session_start=cfg.session.start,
        session_end=cfg.session.end,
        asof_cst=asof_cst,
    )

    symbols = load_symbols(cfg.symbols)

    # As-of date used for OR/RR metrics:
    # - If cfg.asof_date_cst provided: use it
    # - Else: use the last session date in the computed window (current session)
    asof_date_cst = asof_date_cfg if asof_date_cfg else (window.session_dates_cst[-1] if window.session_dates_cst else None)
    if not asof_date_cst:
        raise ValueError("Unable to determine as-of session date")

    print("-" * 60)
    print("RUN: DB-first preparation (candles + opening_ranges + daily_reference_metrics)")
    print(f"Config version: {cfg.version}")
    print(f"Calendar: {cfg.session.calendar}")
    print(f"Session hours: {cfg.session.start} - {cfg.session.end} ({cfg.timezone})")
    print(f"Interval: {interval}")
    print(f"Horizons: {horizons} (fetch max={max_h} sessions)")
    print(f"As-of session date: {asof_date_cst}")
    print(f"Window: {window.start_dt_cst.isoformat()} -> {window.end_dt_cst.isoformat()}")
    print(f"Symbols: {symbols}")
    print("-" * 60)

    conn = connect(cfg.db_path)
    init_db(conn)

    # Single source of truth for backfill logic:
    ensure_asof_ready(conn, cfg, asof_date_cst)

    conn.close()
    print("-" * 60)
    print("[DONE]")
    print("-" * 60)
