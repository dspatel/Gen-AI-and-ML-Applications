from __future__ import annotations

import sqlite3

from .config_loader import AppConfig
from .nyse_calendar import build_session_window
from .symbols import load_symbols
from .db import init_db
from .ingest_yf import run_ingest
from .compute_opening_ranges import compute_opening_ranges
from .compute_reference_metrics import compute_reference_metrics_for_asof, ensure_daily_reference_metrics_table
from .time_utils import combine_cst_date_time


def ensure_asof_ready(conn: sqlite3.Connection, cfg: AppConfig, asof_date_cst: str) -> None:
    """DB-first: ensure candles, opening_ranges, and RR metrics exist for an as-of session date.

    Rules:
    - RR at *start of session* uses ONLY prior sessions (include_today_or=0).
    - Once today's OR is available, RR includes it additively (include_today_or=1).
    - For backtests we compute and store both variants so replay/live can switch seamlessly.
    """
    init_db(conn)
    ensure_daily_reference_metrics_table(conn)

    horizons = cfg.market_data.lookback_days
    max_h = max(horizons)
    interval = cfg.market_data.interval
    or_minutes = cfg.market_data.opening_range_minutes

    asof_cst = combine_cst_date_time(asof_date_cst, cfg.session.end)
    window = build_session_window(
        calendar_name=cfg.session.calendar,
        n=max_h,
        session_start=cfg.session.start,
        session_end=cfg.session.end,
        asof_cst=asof_cst,
    )

    symbols = load_symbols(cfg.symbols)

    # Step 1: candles
    for sym in symbols:
        run_ingest(
            conn=conn,
            symbol=sym,
            interval=interval,
            start_cst=window.start_dt_cst,
            end_cst=window.end_dt_cst,
            session_dates_cst=window.session_dates_cst,
            session_start=cfg.session.start,
            session_end=cfg.session.end,
            provider=cfg.market_data.provider,
            alpaca_feed=cfg.market_data.alpaca_feed,
        )

    # Step 2: ORs
    compute_opening_ranges(
        conn=conn,
        symbols=symbols,
        session_dates_cst=window.session_dates_cst,
        interval=interval,
        or_minutes=or_minutes,
        session_start=cfg.session.start,
        session_end=cfg.session.end,
    )

    # Step 3: RR metrics for as-of (both variants)
    for sym in symbols:
        compute_reference_metrics_for_asof(
            conn=conn,
            symbol=sym,
            asof_date_cst=asof_date_cst,
            horizons=horizons,
            orb_minutes=or_minutes,
            interval=interval,
            session_dates_cst=window.session_dates_cst,
            include_today_or=0,
        )
        compute_reference_metrics_for_asof(
            conn=conn,
            symbol=sym,
            asof_date_cst=asof_date_cst,
            horizons=horizons,
            orb_minutes=or_minutes,
            interval=interval,
            session_dates_cst=window.session_dates_cst,
            include_today_or=1,
        )
