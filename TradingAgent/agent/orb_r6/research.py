from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import pandas_market_calendars as mcal

from .breakout_engine import evaluate_bar_close_only, load_day_candles
from .breakouts import HorizonState, ensure_breakout_tables, load_rr_rows
from .cache_bootstrap import bootstrap_candles_from_orb_cache
from .compute_opening_ranges import compute_opening_ranges
from .compute_reference_metrics import compute_reference_metrics_for_asof, ensure_daily_reference_metrics_table
from .config_loader import load_config
from .db import connect, init_db
from .ingest_yf import run_ingest
from .symbols import load_symbols
from .time_utils import combine_cst_date_time


RISK_PCT_PER_TRADE = 0.005
TARGET_R = 2.0
PARTIAL_TARGET_R = 1.0
NO_PROGRESS_TARGET_R = 0.5
NO_PROGRESS_BARS = 4
MIN_STOP_DISTANCE_PCT = 0.001


@dataclass(frozen=True)
class ResearchConfig:
    config_path: str
    start_date: str
    end_date: str
    symbols: list[str] | None = None


def run_research(config: ResearchConfig) -> dict:
    cfg = load_config(config.config_path)
    conn = connect(cfg.db_path)
    init_db(conn)
    ensure_breakout_tables(conn)
    ensure_daily_reference_metrics_table(conn)
    _ensure_research_tables(conn)

    run_id = str(uuid.uuid4())
    started_at = datetime.utcnow().isoformat()
    try:
        symbols = config.symbols if config.symbols else load_symbols(cfg.symbols)
        interval = cfg.market_data.interval
        provider = cfg.market_data.provider
        if provider != "alpaca":
            raise ValueError(
                f"r6_research requires market_data.provider=alpaca; current={provider!r}"
            )
        or_minutes = int(cfg.market_data.opening_range_minutes)
        horizons = sorted(int(x) for x in cfg.market_data.lookback_days)
        max_h = max(horizons)

        all_dates, asof_dates = _build_session_date_ranges(
            calendar_name=cfg.session.calendar,
            start_date=config.start_date,
            end_date=config.end_date,
            lookback_sessions=max_h,
        )
        if not asof_dates:
            raise ValueError("No trading sessions found in requested date range.")

        for sym in symbols:
            cache_stats = bootstrap_candles_from_orb_cache(
                conn=conn,
                symbol=sym,
                interval=interval,
                session_dates_cst=all_dates,
                session_start=cfg.session.start,
                session_end=cfg.session.end,
                source_db_path=cfg.cache_source_db_path,
            )
            # Backfill only research-window dates. Lookback dates before start_date may be absent
            # in legacy cache and are intentionally not fetched here.
            missing_dates = [d for d in _find_missing_candle_dates(conn, sym, interval, all_dates) if d >= config.start_date]
            if missing_dates:
                miss_start = combine_cst_date_time(missing_dates[0], cfg.session.start)
                miss_end = combine_cst_date_time(missing_dates[-1], cfg.session.end)
                miss_window_dates = [d for d in all_dates if missing_dates[0] <= d <= missing_dates[-1]]
                run_ingest(
                    conn=conn,
                    symbol=sym,
                    interval=interval,
                    start_cst=miss_start,
                    end_cst=miss_end,
                    session_dates_cst=miss_window_dates,
                    session_start=cfg.session.start,
                    session_end=cfg.session.end,
                    provider=cfg.market_data.provider,
                    alpaca_feed=cfg.market_data.alpaca_feed,
                )
            print(
                f"[r6_research] {sym}: cache={cache_stats.get('prepared_rows', 0)} rows "
                f"(ins={cache_stats.get('inserted', 0)}), missing_dates={len(missing_dates)}"
            )

        compute_opening_ranges(
            conn=conn,
            symbols=symbols,
            session_dates_cst=all_dates,
            interval=interval,
            or_minutes=or_minutes,
            session_start=cfg.session.start,
            session_end=cfg.session.end,
        )

        template = {"breakout_default": "{symbol} {direction} H{primary_horizon}"}
        for asof_date in asof_dates:
            session_open = combine_cst_date_time(asof_date, cfg.session.start)
            or_end_ts = pd.Timestamp(session_open + timedelta(minutes=or_minutes))

            for sym in symbols:
                compute_reference_metrics_for_asof(
                    conn=conn,
                    symbol=sym,
                    asof_date_cst=asof_date,
                    horizons=horizons,
                    orb_minutes=or_minutes,
                    interval=interval,
                    session_dates_cst=all_dates,
                    include_today_or=0,
                )
                compute_reference_metrics_for_asof(
                    conn=conn,
                    symbol=sym,
                    asof_date_cst=asof_date,
                    horizons=horizons,
                    orb_minutes=or_minutes,
                    interval=interval,
                    session_dates_cst=all_dates,
                    include_today_or=1,
                )

                rr_pre = load_rr_rows(conn, sym, asof_date, or_minutes, interval, include_today_or=0)
                rr_post = load_rr_rows(conn, sym, asof_date, or_minutes, interval, include_today_or=1)
                rr_seed = rr_pre if rr_pre else rr_post
                if not rr_seed:
                    continue

                day = load_day_candles(conn, sym, interval, asof_date)
                if day.empty:
                    continue

                state_by_phase = {0: {}, 1: {}}
                or_end_ts_cst = pd.Timestamp(or_end_ts)
                for _, row in day.iterrows():
                    phase_for_row = 0 if pd.to_datetime(row["close_ts_cst"]) < or_end_ts_cst else 1
                    evaluate_bar_close_only(
                        conn=conn,
                        templates=template,
                        discord_enabled=False,
                        webhook="",
                        tag="",
                        mode="RESEARCH",
                        symbol=sym,
                        asof_date_cst=asof_date,
                        interval=interval,
                        or_minutes=or_minutes,
                        horizons=horizons,
                        rr_pre=rr_pre,
                        rr_post=rr_post,
                        rr_seed=rr_seed,
                        state_by_h=state_by_phase[phase_for_row],
                        row=row,
                        or_end_ts_cst=or_end_ts_cst,
                    )

        events = _load_events(conn, symbols, interval, or_minutes, config.start_date, config.end_date)
        bars = _load_bars(conn, symbols, interval, config.start_date, config.end_date)

        entry_profiles = [
            ("R6_CONF62_LIMIT1", 0.62, True, False, True),
            ("R6_CONF54_LIMIT1", 0.54, True, False, True),
            ("R6_CONF62_UNLIMITED", 0.62, False, False, True),
            ("R6_CONF62_FLAT_ONLY_LIMIT1", 0.62, True, True, True),
            ("R6_CONF62_LIMIT1_NO_LONG_PREOR", 0.62, True, False, False),
        ]
        exit_ladders = [
            "FIXED_2R_EOD",
            "TIME_STOP_2R",
            "OR_REENTRY_FAIL_2R",
            "RR_REENTRY_FAIL_2R",
            "RR_MID_FAIL_2R",
            "PARTIAL_1R_BE_EMA",
            "EMA20_TRAIL_ONLY",
        ]

        variants: list[VariantSpec] = []
        for entry_id, conf, limit1, flat_only, allow_long_pre_or in entry_profiles:
            for exit_variant in exit_ladders:
                variants.append(
                    VariantSpec(
                        variant_id=f"{entry_id}__{exit_variant}",
                        confidence_min=conf,
                        one_trade_per_day=limit1,
                        flat_only=flat_only,
                        exit_variant=exit_variant,
                        allow_long_pre_or=allow_long_pre_or,
                    )
                )

        all_trades: list[pd.DataFrame] = []
        for spec in variants:
            tdf = _simulate_variant(events, bars, spec)
            all_trades.append(tdf)
        trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()

        metrics = _build_metrics(trades)
        yearly = _build_yearly_returns(trades)
        subset = _build_subset_performance(trades)
        exit_reason_perf = _build_exit_reason_performance(trades)
        confidence_report = _build_confidence_sizing_report(
            trades,
            confidence_floor=0.62,
            confidence_full=0.85,
            confidence_min_multiplier=0.50,
        )
        checks = _build_calculation_checks(events, trades)

        _persist_research_rows(
            conn,
            run_id,
            started_at,
            config,
            symbols,
            interval,
            or_minutes,
            trades,
            metrics,
            yearly,
            subset,
            exit_reason_perf,
            confidence_report,
        )
        summary = _write_outputs(
            run_id,
            config,
            symbols,
            interval,
            events,
            trades,
            metrics,
            yearly,
            subset,
            exit_reason_perf,
            confidence_report,
            checks,
            cfg.research_output_dir,
        )

        conn.execute(
            """
            UPDATE r6_strategy_runs
            SET completed_at=?, status='completed', summary_json=?
            WHERE run_id=?
            """,
            (datetime.utcnow().isoformat(), json.dumps(summary, sort_keys=True), run_id),
        )
        conn.commit()
        conn.close()
        return summary
    except Exception as exc:
        conn.execute(
            """
            INSERT OR REPLACE INTO r6_strategy_runs
            (run_id, started_at, completed_at, status, mode, start_date, end_date, symbols_csv, interval, orb_minutes, summary_json)
            VALUES (?, ?, ?, 'failed', 'research', ?, ?, ?, '', 0, ?)
            """,
            (
                run_id,
                started_at,
                datetime.utcnow().isoformat(),
                config.start_date,
                config.end_date,
                ",".join(config.symbols or []),
                json.dumps({"error": str(exc)}, sort_keys=True),
            ),
        )
        conn.commit()
        conn.close()
        raise


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    confidence_min: float
    one_trade_per_day: bool
    flat_only: bool
    exit_variant: str
    allow_long_pre_or: bool = True


def _ensure_research_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_strategy_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            symbols_csv TEXT NOT NULL,
            interval TEXT NOT NULL,
            orb_minutes INTEGER NOT NULL,
            summary_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_trades (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            exit_variant TEXT,
            symbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_ts TEXT NOT NULL,
            entry_ts TEXT NOT NULL,
            exit_ts TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            risk REAL NOT NULL,
            r_mult REAL NOT NULL,
            ret_pct REAL NOT NULL,
            confidence REAL,
            primary_horizon INTEGER,
            include_today_or INTEGER,
            inflation_factor REAL,
            overlap_pairs_pct REAL,
            ref_width REAL,
            break_confluence INTEGER,
            flat_regime INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_metrics (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            trades_count INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_r REAL NOT NULL,
            profit_factor REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            total_return_pct REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_yearly_returns (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            trades_count INTEGER NOT NULL,
            return_pct REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_subset_performance (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            subset_key TEXT NOT NULL,
            subset_value TEXT NOT NULL,
            trades_count INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_r REAL NOT NULL,
            profit_factor REAL NOT NULL,
            return_pct REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_exit_reason_performance (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            exit_reason TEXT NOT NULL,
            is_stop_exit INTEGER NOT NULL,
            trades_count INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_r REAL NOT NULL,
            profit_factor REAL NOT NULL,
            return_pct REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_confidence_sizing_report (
            run_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            trades_count INTEGER NOT NULL,
            avg_confidence REAL,
            mean_scale REAL,
            baseline_avg_r REAL NOT NULL,
            weighted_avg_r REAL NOT NULL,
            baseline_pf REAL NOT NULL,
            weighted_pf REAL NOT NULL,
            baseline_return_pct REAL NOT NULL,
            weighted_return_pct REAL NOT NULL,
            uplift_return_pct REAL NOT NULL
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info('r6_trades')").fetchall()}
    if "exit_variant" not in cols:
        conn.execute("ALTER TABLE r6_trades ADD COLUMN exit_variant TEXT")
    if "ref_width" not in cols:
        conn.execute("ALTER TABLE r6_trades ADD COLUMN ref_width REAL")
    if "break_confluence" not in cols:
        conn.execute("ALTER TABLE r6_trades ADD COLUMN break_confluence INTEGER")
    conn.commit()


def _build_session_date_ranges(calendar_name: str, start_date: str, end_date: str, lookback_sessions: int) -> tuple[list[str], list[str]]:
    start_d = date.fromisoformat(start_date)
    end_d = date.fromisoformat(end_date)
    cal = mcal.get_calendar(calendar_name)

    seed_start = (start_d - timedelta(days=200)).isoformat()
    sched = cal.schedule(start_date=seed_start, end_date=end_d.isoformat())
    if sched.empty:
        return [], []
    all_dates = [d.date().isoformat() for d in sched.index.to_pydatetime()]
    asof_dates = [d for d in all_dates if start_date <= d <= end_date]
    if not asof_dates:
        return [], []

    first_idx = all_dates.index(asof_dates[0])
    last_idx = all_dates.index(asof_dates[-1])
    start_idx = max(0, first_idx - lookback_sessions)
    return all_dates[start_idx : last_idx + 1], asof_dates


def _find_missing_candle_dates(conn: sqlite3.Connection, symbol: str, interval: str, session_dates: list[str]) -> list[str]:
    if not session_dates:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT cst_date
        FROM candles
        WHERE symbol=?
          AND interval=?
          AND cst_date >= ?
          AND cst_date <= ?
        """,
        (symbol, interval, min(session_dates), max(session_dates)),
    ).fetchall()
    present = {r[0] for r in rows}
    return [d for d in session_dates if d not in present]


def _load_events(
    conn: sqlite3.Connection,
    symbols: list[str],
    interval: str,
    or_minutes: int,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    sym_ph = ",".join(["?"] * len(symbols))
    sql = f"""
    SELECT
        be.event_id,
        be.symbol,
        be.asof_date_cst AS session_date,
        be.bar_close_ts_cst AS signal_ts,
        be.direction,
        be.decision,
        be.confidence,
        be.primary_horizon,
        be.include_today_or,
        be.candle_high,
        be.candle_low,
        be.candle_close,
        eh.breakout_strength,
        eh.close_pen,
        eh.wick_pen,
        eh.body_norm,
        eh.range_norm,
        eh.ref_high,
        eh.ref_low,
        eh.ref_width,
        eh.inflation_factor,
        eh.or_overlap_pairs_pct,
        hc.broke_h_count,
        o.or_high,
        o.or_low
    FROM breakout_events be
    LEFT JOIN event_horizon_metrics eh
      ON be.event_id = eh.event_id AND be.primary_horizon = eh.horizon_days
    LEFT JOIN (
        SELECT event_id, SUM(CASE WHEN did_break=1 THEN 1 ELSE 0 END) AS broke_h_count
        FROM event_horizon_metrics
        GROUP BY event_id
    ) hc
      ON be.event_id = hc.event_id
    LEFT JOIN opening_ranges o
      ON be.symbol = o.symbol
     AND be.asof_date_cst = o.session_date_cst
     AND be.orb_minutes = o.or_minutes
     AND be.interval = o.interval
    WHERE be.interval = ?
      AND be.orb_minutes = ?
      AND be.asof_date_cst >= ?
      AND be.asof_date_cst <= ?
      AND be.symbol IN ({sym_ph})
    ORDER BY be.symbol, be.asof_date_cst, be.bar_close_ts_cst
    """
    params: list[object] = [interval, int(or_minutes), start_date, end_date, *symbols]
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return df
    df["signal_ts"] = pd.to_datetime(df["signal_ts"], utc=True)
    df["flat_regime"] = (
        (df["or_overlap_pairs_pct"].fillna(0.0) >= 0.60)
        & (df["inflation_factor"].fillna(99.0) <= 1.25)
    )
    return df


def _load_bars(
    conn: sqlite3.Connection,
    symbols: list[str],
    interval: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    sym_ph = ",".join(["?"] * len(symbols))
    sql = f"""
    SELECT symbol, cst_date AS session_date, open_ts_cst, close_ts_cst, open, high, low, close
    FROM candles
    WHERE interval = ?
      AND cst_date >= ?
      AND cst_date <= ?
      AND symbol IN ({sym_ph})
    ORDER BY symbol, cst_date, open_ts_cst
    """
    params: list[object] = [interval, start_date, end_date, *symbols]
    bars = pd.read_sql_query(sql, conn, params=params)
    if bars.empty:
        return bars
    bars["open_ts"] = pd.to_datetime(bars["open_ts_cst"], utc=True)
    bars["close_ts"] = pd.to_datetime(bars["close_ts_cst"], utc=True)
    return bars


def _simulate_variant(events: pd.DataFrame, bars: pd.DataFrame, spec: VariantSpec) -> pd.DataFrame:
    columns = [
        "variant_id",
        "exit_variant",
        "symbol",
        "session_date",
        "side",
        "signal_ts",
        "entry_ts",
        "exit_ts",
        "entry_price",
        "stop_price",
        "exit_price",
        "exit_reason",
        "risk",
        "r_mult",
        "ret_pct",
        "confidence",
        "primary_horizon",
        "include_today_or",
        "inflation_factor",
        "overlap_pairs_pct",
        "ref_width",
        "break_confluence",
        "flat_regime",
    ]
    if events.empty or bars.empty:
        return pd.DataFrame(columns=columns)

    base = events[events["decision"].isin(["LONG", "SHORT"])].copy()
    base = base[base["confidence"].fillna(0.0) >= spec.confidence_min]
    if not spec.allow_long_pre_or:
        base = base[~((base["decision"] == "LONG") & (base["include_today_or"] == 0))]
    if spec.flat_only:
        base = base[base["flat_regime"]]
    if base.empty:
        return pd.DataFrame(columns=columns)

    trades: list[dict] = []
    grouped_events = base.groupby(["symbol", "session_date"], sort=True)
    bars_by_key = {k: g.reset_index(drop=True) for k, g in bars.groupby(["symbol", "session_date"], sort=False)}

    for key, sigs in grouped_events:
        day_bars = bars_by_key.get(key)
        if day_bars is None or day_bars.empty:
            continue
        day_bars = day_bars.copy()
        day_bars["ema20"] = day_bars["close"].astype(float).ewm(span=20, adjust=False).mean()
        day_bars["ema20_prev"] = day_bars["ema20"].shift(1).fillna(day_bars["ema20"])
        sigs = sigs.sort_values("signal_ts")
        if spec.one_trade_per_day:
            sigs = sigs.iloc[:1]

        open_ts_vals = day_bars["open_ts"].tolist()
        last_exit_ts = None

        for _, ev in sigs.iterrows():
            signal_ts = pd.Timestamp(ev["signal_ts"])
            if last_exit_ts is not None and signal_ts <= last_exit_ts:
                continue

            entry_idx = _find_next_index(open_ts_vals, signal_ts)
            if entry_idx is None:
                continue

            side = str(ev["decision"])
            entry_row = day_bars.iloc[entry_idx]
            entry_price = float(entry_row["open"])
            raw_stop = float(ev["candle_low"]) - 0.01 if side == "LONG" else float(ev["candle_high"]) + 0.01
            # Ensure stop remains protective if next-bar entry gaps beyond trigger candle extremes.
            if side == "LONG":
                stop_price = min(raw_stop, entry_price - 0.01)
            else:
                stop_price = max(raw_stop, entry_price + 0.01)
            min_stop_dist = entry_price * MIN_STOP_DISTANCE_PCT
            if side == "LONG":
                stop_price = min(stop_price, entry_price - min_stop_dist)
            else:
                stop_price = max(stop_price, entry_price + min_stop_dist)
            risk = abs(entry_price - stop_price)
            if risk <= 0:
                continue
            outcome = _simulate_exit(
                day_bars=day_bars,
                entry_idx=entry_idx,
                side=side,
                entry_price=entry_price,
                stop_price=stop_price,
                risk=risk,
                exit_variant=spec.exit_variant,
                or_high=(float(ev["or_high"]) if ev.get("or_high") is not None else None),
                or_low=(float(ev["or_low"]) if ev.get("or_low") is not None else None),
                rr_high=(float(ev["ref_high"]) if ev.get("ref_high") is not None else None),
                rr_low=(float(ev["ref_low"]) if ev.get("ref_low") is not None else None),
            )
            exit_ts = outcome["exit_ts"]
            exit_reason = outcome["exit_reason"]
            r_mult = float(outcome["r_mult"])
            exit_price = float(_effective_exit_price(side, entry_price, risk, r_mult))
            ret_pct = r_mult * RISK_PCT_PER_TRADE
            trades.append(
                {
                    "variant_id": spec.variant_id,
                    "exit_variant": spec.exit_variant,
                    "symbol": key[0],
                    "session_date": key[1],
                    "side": side,
                    "signal_ts": signal_ts.isoformat(),
                    "entry_ts": pd.Timestamp(entry_row["open_ts"]).isoformat(),
                    "exit_ts": exit_ts.isoformat(),
                    "entry_price": entry_price,
                    "stop_price": stop_price,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "risk": risk,
                    "r_mult": r_mult,
                    "ret_pct": ret_pct,
                    "confidence": float(ev["confidence"]) if ev["confidence"] is not None else None,
                    "primary_horizon": int(ev["primary_horizon"]) if ev["primary_horizon"] is not None else None,
                    "include_today_or": int(ev["include_today_or"]) if ev["include_today_or"] is not None else None,
                    "inflation_factor": float(ev["inflation_factor"]) if ev["inflation_factor"] is not None else None,
                    "overlap_pairs_pct": float(ev["or_overlap_pairs_pct"]) if ev["or_overlap_pairs_pct"] is not None else None,
                    "ref_width": float(ev["ref_width"]) if ev.get("ref_width") is not None else None,
                    "break_confluence": int(ev["broke_h_count"]) if ev.get("broke_h_count") is not None else None,
                    "flat_regime": int(bool(ev["flat_regime"])),
                }
            )
            last_exit_ts = exit_ts

    return pd.DataFrame(trades, columns=columns)


def _simulate_exit(
    *,
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
    exit_variant: str,
    or_high: float | None,
    or_low: float | None,
    rr_high: float | None,
    rr_low: float | None,
) -> dict:
    if exit_variant == "FIXED_2R_EOD":
        return _exit_fixed_2r(day_bars, entry_idx, side, entry_price, stop_price, risk)
    if exit_variant == "TIME_STOP_2R":
        return _exit_time_stop_2r(day_bars, entry_idx, side, entry_price, stop_price, risk)
    if exit_variant == "OR_REENTRY_FAIL_2R":
        return _exit_or_reentry_2r(day_bars, entry_idx, side, entry_price, stop_price, risk, or_high, or_low)
    if exit_variant == "RR_REENTRY_FAIL_2R":
        return _exit_rr_reentry_2r(day_bars, entry_idx, side, entry_price, stop_price, risk, rr_high, rr_low)
    if exit_variant == "RR_MID_FAIL_2R":
        return _exit_rr_mid_fail_2r(day_bars, entry_idx, side, entry_price, stop_price, risk, rr_high, rr_low)
    if exit_variant == "PARTIAL_1R_BE_EMA":
        return _exit_partial_1r_be_ema(day_bars, entry_idx, side, entry_price, stop_price, risk)
    if exit_variant == "EMA20_TRAIL_ONLY":
        return _exit_ema_trail_only(day_bars, entry_idx, side, entry_price, stop_price, risk)
    raise ValueError(f"Unknown exit_variant: {exit_variant}")


def _exit_fixed_2r(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
) -> dict:
    target = entry_price + TARGET_R * risk if side == "LONG" else entry_price - TARGET_R * risk
    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        bar_close = pd.Timestamp(bar["close_ts"])
        if side == "LONG":
            stop_hit = low <= stop_price
            target_hit = high >= target
        else:
            stop_hit = high >= stop_price
            target_hit = low <= target
        if stop_hit and target_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP_SAME_BAR", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if stop_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if target_hit:
            return {"exit_ts": bar_close, "exit_reason": "TARGET_2R", "r_mult": TARGET_R}

    eod = day_bars.iloc[-1]
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD",
        "r_mult": _r_at_price(side, entry_price, float(eod["close"]), risk),
    }


def _exit_time_stop_2r(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
) -> dict:
    target = entry_price + TARGET_R * risk if side == "LONG" else entry_price - TARGET_R * risk
    progress_lvl = entry_price + NO_PROGRESS_TARGET_R * risk if side == "LONG" else entry_price - NO_PROGRESS_TARGET_R * risk
    progress_hit = False
    bars_since_entry = 0

    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        bar_close = pd.Timestamp(bar["close_ts"])
        bars_since_entry += 1

        if side == "LONG":
            if high >= progress_lvl:
                progress_hit = True
            stop_hit = low <= stop_price
            target_hit = high >= target
        else:
            if low <= progress_lvl:
                progress_hit = True
            stop_hit = high >= stop_price
            target_hit = low <= target

        if stop_hit and target_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP_SAME_BAR", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if stop_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if target_hit:
            return {"exit_ts": bar_close, "exit_reason": "TARGET_2R", "r_mult": TARGET_R}
        if bars_since_entry >= NO_PROGRESS_BARS and (not progress_hit):
            return {
                "exit_ts": bar_close,
                "exit_reason": "TIME_STOP_NO_PROGRESS",
                "r_mult": _r_at_price(side, entry_price, close, risk),
            }

    eod = day_bars.iloc[-1]
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD",
        "r_mult": _r_at_price(side, entry_price, float(eod["close"]), risk),
    }


def _exit_or_reentry_2r(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
    or_high: float | None,
    or_low: float | None,
) -> dict:
    target = entry_price + TARGET_R * risk if side == "LONG" else entry_price - TARGET_R * risk
    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        bar_close = pd.Timestamp(bar["close_ts"])
        if side == "LONG":
            stop_hit = low <= stop_price
            target_hit = high >= target
            reentry = (or_high is not None) and (close < float(or_high))
        else:
            stop_hit = high >= stop_price
            target_hit = low <= target
            reentry = (or_low is not None) and (close > float(or_low))

        if stop_hit and target_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP_SAME_BAR", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if stop_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if target_hit:
            return {"exit_ts": bar_close, "exit_reason": "TARGET_2R", "r_mult": TARGET_R}
        if reentry:
            return {
                "exit_ts": bar_close,
                "exit_reason": "OR_REENTRY_FAIL",
                "r_mult": _r_at_price(side, entry_price, close, risk),
            }

    eod = day_bars.iloc[-1]
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD",
        "r_mult": _r_at_price(side, entry_price, float(eod["close"]), risk),
    }


def _exit_rr_reentry_2r(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
    rr_high: float | None,
    rr_low: float | None,
) -> dict:
    target = entry_price + TARGET_R * risk if side == "LONG" else entry_price - TARGET_R * risk
    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        bar_close = pd.Timestamp(bar["close_ts"])
        if side == "LONG":
            stop_hit = low <= stop_price
            target_hit = high >= target
            reentry = (rr_high is not None) and (close < float(rr_high))
        else:
            stop_hit = high >= stop_price
            target_hit = low <= target
            reentry = (rr_low is not None) and (close > float(rr_low))

        if stop_hit and target_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP_SAME_BAR", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if stop_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if target_hit:
            return {"exit_ts": bar_close, "exit_reason": "TARGET_2R", "r_mult": TARGET_R}
        if reentry:
            return {
                "exit_ts": bar_close,
                "exit_reason": "RR_REENTRY_FAIL",
                "r_mult": _r_at_price(side, entry_price, close, risk),
            }

    eod = day_bars.iloc[-1]
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD",
        "r_mult": _r_at_price(side, entry_price, float(eod["close"]), risk),
    }


def _exit_rr_mid_fail_2r(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
    rr_high: float | None,
    rr_low: float | None,
) -> dict:
    target = entry_price + TARGET_R * risk if side == "LONG" else entry_price - TARGET_R * risk
    rr_mid = None
    if rr_high is not None and rr_low is not None:
        rr_mid = (float(rr_high) + float(rr_low)) / 2.0
    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        bar_close = pd.Timestamp(bar["close_ts"])
        if side == "LONG":
            stop_hit = low <= stop_price
            target_hit = high >= target
            mid_fail = (rr_mid is not None) and (close < rr_mid)
        else:
            stop_hit = high >= stop_price
            target_hit = low <= target
            mid_fail = (rr_mid is not None) and (close > rr_mid)

        if stop_hit and target_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP_SAME_BAR", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if stop_hit:
            stop_fill = _stop_fill_price(side, stop_price, bar_open)
            return {"exit_ts": bar_close, "exit_reason": "STOP", "r_mult": _r_at_price(side, entry_price, stop_fill, risk)}
        if target_hit:
            return {"exit_ts": bar_close, "exit_reason": "TARGET_2R", "r_mult": TARGET_R}
        if mid_fail:
            return {
                "exit_ts": bar_close,
                "exit_reason": "RR_MID_FAIL",
                "r_mult": _r_at_price(side, entry_price, close, risk),
            }

    eod = day_bars.iloc[-1]
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD",
        "r_mult": _r_at_price(side, entry_price, float(eod["close"]), risk),
    }


def _exit_partial_1r_be_ema(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
) -> dict:
    target1 = entry_price + PARTIAL_TARGET_R * risk if side == "LONG" else entry_price - PARTIAL_TARGET_R * risk
    rem_qty = 1.0
    realized_r = 0.0
    partial_taken = False
    trail = stop_price

    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        ema_prev = float(bar["ema20_prev"])
        bar_close = pd.Timestamp(bar["close_ts"])

        if partial_taken:
            if side == "LONG":
                trail = max(trail, entry_price, ema_prev)
            else:
                trail = min(trail, entry_price, ema_prev)

        if side == "LONG":
            stop_hit = low <= trail
        else:
            stop_hit = high >= trail
        if stop_hit:
            stop_fill = _stop_fill_price(side, trail, bar_open)
            realized_r += rem_qty * _r_at_price(side, entry_price, stop_fill, risk)
            return {
                "exit_ts": bar_close,
                "exit_reason": "TRAIL_EMA_STOP" if partial_taken else "STOP",
                "r_mult": realized_r,
            }

        if (not partial_taken):
            target1_hit = high >= target1 if side == "LONG" else low <= target1
            if target1_hit:
                realized_r += 0.5 * PARTIAL_TARGET_R
                rem_qty = 0.5
                partial_taken = True

    eod = day_bars.iloc[-1]
    eod_close = float(eod["close"])
    realized_r += rem_qty * _r_at_price(side, entry_price, eod_close, risk)
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD_PARTIAL" if partial_taken else "EOD",
        "r_mult": realized_r,
    }


def _exit_ema_trail_only(
    day_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    entry_price: float,
    stop_price: float,
    risk: float,
) -> dict:
    trail = stop_price
    for j in range(entry_idx, len(day_bars)):
        bar = day_bars.iloc[j]
        bar_open = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        ema_prev = float(bar["ema20_prev"])
        bar_close = pd.Timestamp(bar["close_ts"])
        if side == "LONG":
            trail = max(trail, ema_prev)
            if low <= trail:
                stop_fill = _stop_fill_price(side, trail, bar_open)
                return {
                    "exit_ts": bar_close,
                    "exit_reason": "TRAIL_EMA_STOP",
                    "r_mult": _r_at_price(side, entry_price, stop_fill, risk),
                }
        else:
            trail = min(trail, ema_prev)
            if high >= trail:
                stop_fill = _stop_fill_price(side, trail, bar_open)
                return {
                    "exit_ts": bar_close,
                    "exit_reason": "TRAIL_EMA_STOP",
                    "r_mult": _r_at_price(side, entry_price, stop_fill, risk),
                }
    eod = day_bars.iloc[-1]
    return {
        "exit_ts": pd.Timestamp(eod["close_ts"]),
        "exit_reason": "EOD",
        "r_mult": _r_at_price(side, entry_price, float(eod["close"]), risk),
    }


def _r_at_price(side: str, entry_price: float, exit_price: float, risk: float) -> float:
    if side == "LONG":
        return (exit_price - entry_price) / risk
    return (entry_price - exit_price) / risk


def _effective_exit_price(side: str, entry_price: float, risk: float, r_mult: float) -> float:
    if side == "LONG":
        return entry_price + r_mult * risk
    return entry_price - r_mult * risk


def _stop_fill_price(side: str, stop_price: float, bar_open: float) -> float:
    if side == "LONG":
        return min(stop_price, bar_open)
    return max(stop_price, bar_open)


def _find_next_index(ordered_ts: list[pd.Timestamp], signal_ts: pd.Timestamp) -> int | None:
    for i, ts in enumerate(ordered_ts):
        if ts > signal_ts:
            return i
    return None


def _build_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    cols = ["variant_id", "trades_count", "win_rate", "avg_r", "profit_factor", "max_drawdown_pct", "total_return_pct"]
    if trades.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict] = []
    for variant_id, grp in trades.groupby("variant_id", sort=True):
        g = grp.sort_values("exit_ts").copy()
        g["equity"] = (1.0 + g["ret_pct"]).cumprod()
        dd = g["equity"] / g["equity"].cummax() - 1.0
        pnl_r = g["r_mult"].astype(float)
        gp = float(pnl_r[pnl_r > 0].sum())
        gl = abs(float(pnl_r[pnl_r < 0].sum()))
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        rows.append(
            {
                "variant_id": variant_id,
                "trades_count": int(len(g)),
                "win_rate": float((pnl_r > 0).mean()),
                "avg_r": float(pnl_r.mean()),
                "profit_factor": float(pf),
                "max_drawdown_pct": float(abs(dd.min()) * 100.0) if len(dd) else 0.0,
                "total_return_pct": float((g["equity"].iloc[-1] - 1.0) * 100.0),
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values(["total_return_pct", "avg_r"], ascending=[False, False])


def _build_yearly_returns(trades: pd.DataFrame) -> pd.DataFrame:
    cols = ["variant_id", "year", "trades_count", "return_pct"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    df = trades.copy()
    df["year"] = pd.to_datetime(df["exit_ts"]).dt.year
    rows: list[dict] = []
    for (variant_id, year), grp in df.groupby(["variant_id", "year"], sort=True):
        ret = float((1.0 + grp["ret_pct"]).prod() - 1.0) * 100.0
        rows.append(
            {
                "variant_id": variant_id,
                "year": int(year),
                "trades_count": int(len(grp)),
                "return_pct": ret,
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values(["variant_id", "year"])


def _build_subset_performance(trades: pd.DataFrame) -> pd.DataFrame:
    cols = ["variant_id", "subset_key", "subset_value", "trades_count", "win_rate", "avg_r", "profit_factor", "return_pct"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    out: list[dict] = []
    for variant_id, vdf in trades.groupby("variant_id", sort=True):
        out.extend(_subset_rows(variant_id, vdf, "flat_regime", {"0": vdf[vdf["flat_regime"] == 0], "1": vdf[vdf["flat_regime"] == 1]}))
        out.extend(
            _subset_rows(
                variant_id,
                vdf,
                "rr_phase",
                {
                    "pre_or": vdf[vdf["include_today_or"] == 0],
                    "post_or": vdf[vdf["include_today_or"] == 1],
                },
            )
        )
        out.extend(
            _subset_rows(
                variant_id,
                vdf,
                "direction",
                {
                    "LONG": vdf[vdf["side"] == "LONG"],
                    "SHORT": vdf[vdf["side"] == "SHORT"],
                },
            )
        )
        out.extend(
            _subset_rows(
                variant_id,
                vdf,
                "primary_horizon",
                {str(int(h)): g for h, g in vdf.groupby("primary_horizon", dropna=True)},
            )
        )
        infl = vdf.copy()
        infl["infl_bucket"] = infl["inflation_factor"].apply(_inflation_bucket)
        out.extend(
            _subset_rows(
                variant_id,
                infl,
                "inflation_bucket",
                {str(k): g for k, g in infl.groupby("infl_bucket", dropna=True)},
            )
        )
        ov = vdf.copy()
        ov["overlap_bucket"] = ov["overlap_pairs_pct"].apply(_overlap_bucket)
        out.extend(
            _subset_rows(
                variant_id,
                ov,
                "overlap_bucket",
                {str(k): g for k, g in ov.groupby("overlap_bucket", dropna=True)},
            )
        )
        rw = vdf.copy()
        rw["ref_width_bucket"] = _refwidth_bucket_series(rw["ref_width"])
        out.extend(
            _subset_rows(
                variant_id,
                rw,
                "ref_width_bucket",
                {str(k): g for k, g in rw.groupby("ref_width_bucket", dropna=True)},
            )
        )
        cf = vdf.copy()
        cf["confluence_bucket"] = cf["break_confluence"].apply(_confluence_bucket)
        out.extend(
            _subset_rows(
                variant_id,
                cf,
                "confluence_bucket",
                {str(k): g for k, g in cf.groupby("confluence_bucket", dropna=True)},
            )
        )
    return pd.DataFrame(out, columns=cols).sort_values(["variant_id", "subset_key", "subset_value"])


def _build_exit_reason_performance(trades: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant_id",
        "exit_reason",
        "is_stop_exit",
        "trades_count",
        "win_rate",
        "avg_r",
        "profit_factor",
        "return_pct",
    ]
    if trades.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict] = []
    stop_reasons = {"STOP", "STOP_SAME_BAR", "TRAIL_EMA_STOP"}
    for (variant_id, exit_reason), grp in trades.groupby(["variant_id", "exit_reason"], sort=True):
        pnl_r = grp["r_mult"].astype(float)
        gp = float(pnl_r[pnl_r > 0].sum())
        gl = abs(float(pnl_r[pnl_r < 0].sum()))
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        ret = float((1.0 + grp["ret_pct"]).prod() - 1.0) * 100.0
        rows.append(
            {
                "variant_id": variant_id,
                "exit_reason": str(exit_reason),
                "is_stop_exit": int(str(exit_reason) in stop_reasons),
                "trades_count": int(len(grp)),
                "win_rate": float((pnl_r > 0).mean()),
                "avg_r": float(pnl_r.mean()),
                "profit_factor": float(pf),
                "return_pct": ret,
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values(["variant_id", "is_stop_exit", "exit_reason"], ascending=[True, False, True])


def _confidence_trade_scale(conf: float | None, floor: float, full: float, min_mult: float) -> float:
    if conf is None or pd.isna(conf):
        return max(0.0, min(1.0, float(min_mult)))
    lo = float(floor)
    hi = max(float(full), lo + 1e-6)
    base = max(0.0, min(1.0, float(min_mult)))
    x = (float(conf) - lo) / (hi - lo)
    x = max(0.0, min(1.0, x))
    return base + (1.0 - base) * x


def _build_confidence_sizing_report(
    trades: pd.DataFrame,
    *,
    confidence_floor: float,
    confidence_full: float,
    confidence_min_multiplier: float,
) -> pd.DataFrame:
    cols = [
        "variant_id",
        "trades_count",
        "avg_confidence",
        "mean_scale",
        "baseline_avg_r",
        "weighted_avg_r",
        "baseline_pf",
        "weighted_pf",
        "baseline_return_pct",
        "weighted_return_pct",
        "uplift_return_pct",
    ]
    if trades.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict] = []
    for variant_id, grp in trades.groupby("variant_id", sort=True):
        g = grp.sort_values("exit_ts").copy()
        g["confidence"] = g["confidence"].astype(float)
        g["scale"] = g["confidence"].apply(
            lambda c: _confidence_trade_scale(c, confidence_floor, confidence_full, confidence_min_multiplier)
        )
        g["base_ret"] = g["r_mult"].astype(float) * RISK_PCT_PER_TRADE
        g["w_ret"] = g["r_mult"].astype(float) * RISK_PCT_PER_TRADE * g["scale"]

        base_equity = (1.0 + g["base_ret"]).cumprod()
        weighted_equity = (1.0 + g["w_ret"]).cumprod()
        base_ret_pct = float((base_equity.iloc[-1] - 1.0) * 100.0) if len(base_equity) else 0.0
        weighted_ret_pct = float((weighted_equity.iloc[-1] - 1.0) * 100.0) if len(weighted_equity) else 0.0

        base_r = g["r_mult"].astype(float)
        w_r = base_r * g["scale"]
        base_gp = float(base_r[base_r > 0].sum())
        base_gl = abs(float(base_r[base_r < 0].sum()))
        weighted_gp = float(w_r[w_r > 0].sum())
        weighted_gl = abs(float(w_r[w_r < 0].sum()))
        base_pf = (base_gp / base_gl) if base_gl > 0 else (float("inf") if base_gp > 0 else 0.0)
        weighted_pf = (weighted_gp / weighted_gl) if weighted_gl > 0 else (float("inf") if weighted_gp > 0 else 0.0)

        rows.append(
            {
                "variant_id": str(variant_id),
                "trades_count": int(len(g)),
                "avg_confidence": float(g["confidence"].mean()) if len(g) else None,
                "mean_scale": float(g["scale"].mean()) if len(g) else None,
                "baseline_avg_r": float(base_r.mean()) if len(base_r) else 0.0,
                "weighted_avg_r": float(w_r.mean()) if len(w_r) else 0.0,
                "baseline_pf": float(base_pf),
                "weighted_pf": float(weighted_pf),
                "baseline_return_pct": base_ret_pct,
                "weighted_return_pct": weighted_ret_pct,
                "uplift_return_pct": float(weighted_ret_pct - base_ret_pct),
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values(["uplift_return_pct", "weighted_return_pct"], ascending=[False, False])


def _build_calculation_checks(events: pd.DataFrame, trades: pd.DataFrame) -> dict:
    checks: dict[str, int | float] = {}
    if trades.empty:
        return {
            "events_count": int(len(events)),
            "trades_count": 0,
            "risk_le_zero": 0,
            "entry_not_after_signal": 0,
            "ret_pct_formula_mismatch": 0,
            "limit1_violations": 0,
            "rr_exit_missing_rr_bounds": 0,
            "extreme_r_over_10_abs": 0,
        }

    t = trades.copy()
    t["entry_ts_dt"] = pd.to_datetime(t["entry_ts"], utc=True, errors="coerce")
    t["signal_ts_dt"] = pd.to_datetime(t["signal_ts"], utc=True, errors="coerce")
    t["ret_formula"] = t["r_mult"].astype(float) * RISK_PCT_PER_TRADE

    checks["events_count"] = int(len(events))
    checks["trades_count"] = int(len(t))
    checks["risk_le_zero"] = int((t["risk"].astype(float) <= 0).sum())
    checks["entry_not_after_signal"] = int((t["entry_ts_dt"] <= t["signal_ts_dt"]).fillna(True).sum())
    checks["ret_pct_formula_mismatch"] = int((abs(t["ret_pct"].astype(float) - t["ret_formula"]) > 1e-9).sum())
    checks["extreme_r_over_10_abs"] = int((t["r_mult"].astype(float).abs() > 10.0).sum())

    limit1 = t[t["variant_id"].str.contains("_LIMIT1__", regex=False)]
    if limit1.empty:
        checks["limit1_violations"] = 0
    else:
        ct = limit1.groupby(["variant_id", "symbol", "session_date"]).size()
        checks["limit1_violations"] = int((ct > 1).sum())

    rr_variants = t[t["exit_variant"].isin(["RR_REENTRY_FAIL_2R", "RR_MID_FAIL_2R"])]
    if rr_variants.empty:
        checks["rr_exit_missing_rr_bounds"] = 0
    else:
        missing = rr_variants["ref_width"].isna() | (rr_variants["ref_width"].astype(float) <= 0)
        checks["rr_exit_missing_rr_bounds"] = int(missing.sum())

    return checks


def _subset_rows(variant_id: str, _: pd.DataFrame, subset_key: str, splits: dict[str, pd.DataFrame]) -> list[dict]:
    rows: list[dict] = []
    for label, part in splits.items():
        if part.empty:
            continue
        pnl_r = part["r_mult"].astype(float)
        gp = float(pnl_r[pnl_r > 0].sum())
        gl = abs(float(pnl_r[pnl_r < 0].sum()))
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        ret = float((1.0 + part["ret_pct"]).prod() - 1.0) * 100.0
        rows.append(
            {
                "variant_id": variant_id,
                "subset_key": subset_key,
                "subset_value": str(label),
                "trades_count": int(len(part)),
                "win_rate": float((pnl_r > 0).mean()),
                "avg_r": float(pnl_r.mean()),
                "profit_factor": float(pf),
                "return_pct": ret,
            }
        )
    return rows


def _inflation_bucket(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "na"
    x = float(v)
    if x < 1.25:
        return "tight"
    if x <= 2.25:
        return "balanced"
    return "stretched"


def _overlap_bucket(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "na"
    x = float(v)
    if x <= 0.30:
        return "shifting"
    if x < 0.60:
        return "mixed"
    return "clustered"


def _refwidth_bucket_series(s: pd.Series) -> pd.Series:
    if s.dropna().shape[0] < 3:
        return s.apply(lambda x: "na" if pd.isna(x) else "all")
    q = s.rank(pct=True, method="average")
    return q.apply(
        lambda v: "na"
        if pd.isna(v)
        else ("p0_30" if v <= 0.30 else ("p30_70" if v <= 0.70 else "p70_100"))
    )


def _confluence_bucket(v: float | int | None) -> str:
    if v is None or pd.isna(v):
        return "na"
    x = int(v)
    if x <= 1:
        return "single"
    if x == 2:
        return "double"
    return "triple_plus"


def _persist_research_rows(
    conn: sqlite3.Connection,
    run_id: str,
    started_at: str,
    config: ResearchConfig,
    symbols: list[str],
    interval: str,
    orb_minutes: int,
    trades: pd.DataFrame,
    metrics: pd.DataFrame,
    yearly: pd.DataFrame,
    subset: pd.DataFrame,
    exit_reason_perf: pd.DataFrame,
    confidence_report: pd.DataFrame,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO r6_strategy_runs
        (run_id, started_at, status, mode, start_date, end_date, symbols_csv, interval, orb_minutes)
        VALUES (?, ?, 'running', 'research', ?, ?, ?, ?, ?)
        """,
        (run_id, started_at, config.start_date, config.end_date, ",".join(symbols), interval, int(orb_minutes)),
    )
    conn.execute("DELETE FROM r6_trades WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM r6_metrics WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM r6_yearly_returns WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM r6_subset_performance WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM r6_exit_reason_performance WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM r6_confidence_sizing_report WHERE run_id = ?", (run_id,))

    if not trades.empty:
        payload = trades.copy()
        payload.insert(0, "run_id", run_id)
        payload.to_sql("r6_trades", conn, if_exists="append", index=False)
    if not metrics.empty:
        payload = metrics.copy()
        payload.insert(0, "run_id", run_id)
        payload.to_sql("r6_metrics", conn, if_exists="append", index=False)
    if not yearly.empty:
        payload = yearly.copy()
        payload.insert(0, "run_id", run_id)
        payload.to_sql("r6_yearly_returns", conn, if_exists="append", index=False)
    if not subset.empty:
        payload = subset.copy()
        payload.insert(0, "run_id", run_id)
        payload.to_sql("r6_subset_performance", conn, if_exists="append", index=False)
    if not exit_reason_perf.empty:
        payload = exit_reason_perf.copy()
        payload.insert(0, "run_id", run_id)
        payload.to_sql("r6_exit_reason_performance", conn, if_exists="append", index=False)
    if not confidence_report.empty:
        payload = confidence_report.copy()
        payload.insert(0, "run_id", run_id)
        payload.to_sql("r6_confidence_sizing_report", conn, if_exists="append", index=False)
    conn.commit()


def _write_outputs(
    run_id: str,
    config: ResearchConfig,
    symbols: Iterable[str],
    interval: str,
    events: pd.DataFrame,
    trades: pd.DataFrame,
    metrics: pd.DataFrame,
    yearly: pd.DataFrame,
    subset: pd.DataFrame,
    exit_reason_perf: pd.DataFrame,
    confidence_report: pd.DataFrame,
    checks: dict,
    output_dir: str,
) -> dict:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    events_path = out_dir / "r6_events.csv"
    trades_path = out_dir / "r6_trades.csv"
    metrics_path = out_dir / "r6_variant_metrics.csv"
    yearly_path = out_dir / "r6_yearly_returns.csv"
    subset_path = out_dir / "r6_subset_performance.csv"
    exit_reason_path = out_dir / "r6_exit_reason_performance.csv"
    confidence_report_path = out_dir / "r6_confidence_sizing_report.csv"
    checks_path = out_dir / "r6_calculation_checks.json"
    summary_path = out_dir / "r6_summary.json"

    events.to_csv(events_path, index=False)
    trades.to_csv(trades_path, index=False)
    metrics.to_csv(metrics_path, index=False)
    yearly.to_csv(yearly_path, index=False)
    subset.to_csv(subset_path, index=False)
    exit_reason_perf.to_csv(exit_reason_path, index=False)
    confidence_report.to_csv(confidence_report_path, index=False)
    checks_path.write_text(json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8")

    best = metrics.iloc[0].to_dict() if not metrics.empty else None
    best_conf = confidence_report.iloc[0].to_dict() if not confidence_report.empty else None
    summary = {
        "run_id": run_id,
        "status": "completed",
        "start_date": config.start_date,
        "end_date": config.end_date,
        "symbols": list(symbols),
        "interval": interval,
        "events_count": int(len(events)),
        "trades_count": int(len(trades)),
        "variants_tested": int(metrics.shape[0]),
        "best_variant": best,
        "confidence_sizing_backtest": best_conf,
        "calculation_checks": checks,
        "artifacts": {
            "events": str(events_path).replace("\\", "/"),
            "trades": str(trades_path).replace("\\", "/"),
            "metrics": str(metrics_path).replace("\\", "/"),
            "yearly": str(yearly_path).replace("\\", "/"),
            "subset": str(subset_path).replace("\\", "/"),
            "exit_reason_performance": str(exit_reason_path).replace("\\", "/"),
            "confidence_sizing_report": str(confidence_report_path).replace("\\", "/"),
            "calculation_checks": str(checks_path).replace("\\", "/"),
            "summary": str(summary_path).replace("\\", "/"),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary
