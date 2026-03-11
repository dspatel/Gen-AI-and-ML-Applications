from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .cache_bootstrap import copy_15m_candles_from_r6
from .alpaca_ingest import ingest_from_alpaca
from .config_loader import AppConfig, StrategyConfig, load_config
from .db import connect, init_db
from .symbols import load_symbols

CST = ZoneInfo("America/Chicago")


@dataclass(frozen=True)
class ResearchConfig:
    config_path: str
    start_date: str
    end_date: str
    symbols: list[str] | None = None
    variant_ids: list[str] | None = None


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    cross_lookback_days: int
    flat_threshold: float
    entry_variant: str
    exit_variant: str
    daily_invalidation_days: int | None = None
    atr_trail_mult: float | None = None
    time_stop_days: int | None = None
    chop_cross_count_max: int = 999
    max_open_positions: int = 8
    max_new_entries_per_day: int = 8


def run_research(config: ResearchConfig) -> dict:
    cfg = load_config(config.config_path)
    conn = connect(cfg.db_path)
    init_db(conn)

    run_id = str(uuid.uuid4())
    started_at = datetime.now(CST).isoformat()
    symbols = config.symbols if config.symbols else load_symbols(cfg.symbols)
    if not symbols:
        raise ValueError("No symbols available for EMA20 research run")

    conn.execute(
        """
        INSERT OR REPLACE INTO ema20_strategy_runs
        (run_id, started_at, status, config_path, start_date, end_date, symbols_csv, variant_count, trades_count)
        VALUES (?, ?, 'running', ?, ?, ?, ?, 0, 0)
        """,
        (
            run_id,
            started_at,
            config.config_path,
            config.start_date,
            config.end_date,
            ",".join(symbols),
        ),
    )
    conn.commit()

    try:
        history_start_date = _history_start_date(config.start_date)
        provider = str(cfg.market_data.provider).strip().lower()
        data_sync_stats: dict[str, object] = {"provider": provider}
        if provider in {"r6", "r6_cache", "cache"}:
            data_sync_stats["r6_copy"] = copy_15m_candles_from_r6(
                conn=conn,
                source_db_path=cfg.market_data.source_r6_db_path,
                symbols=symbols,
                start_date=history_start_date,
                end_date=config.end_date,
            )
        elif provider == "alpaca":
            data_sync_stats["alpaca_ingest"] = ingest_from_alpaca(
                conn=conn,
                symbols=symbols,
                interval=cfg.market_data.interval,
                start_date=history_start_date,
                end_date=config.end_date,
                session_start=cfg.session.start,
                session_end=cfg.session.end,
                feed=cfg.market_data.alpaca_feed,
            )
        elif provider == "auto":
            data_sync_stats["r6_copy"] = copy_15m_candles_from_r6(
                conn=conn,
                source_db_path=cfg.market_data.source_r6_db_path,
                symbols=symbols,
                start_date=history_start_date,
                end_date=config.end_date,
            )
            data_sync_stats["alpaca_ingest"] = ingest_from_alpaca(
                conn=conn,
                symbols=symbols,
                interval=cfg.market_data.interval,
                start_date=history_start_date,
                end_date=config.end_date,
                session_start=cfg.session.start,
                session_end=cfg.session.end,
                feed=cfg.market_data.alpaca_feed,
            )
        else:
            raise ValueError(f"Unsupported EMA20 market_data.provider: {provider!r}")

        symbol_inputs: dict[str, dict] = {}
        for sym in symbols:
            bars = _load_15m_bars(
                conn=conn,
                symbol=sym,
                start_date=history_start_date,
                end_date=config.end_date,
                session_start=cfg.session.start,
                session_end=cfg.session.end,
            )
            if bars.empty:
                continue
            daily = _build_daily_from_15m(bars, atr_period=cfg.strategy.atr_period)
            if daily.empty or len(daily) < (cfg.strategy.secondary_window_days + 5):
                continue
            monthly = _build_monthly_from_daily(daily)
            context_by_lookback = {
                lb: _build_day_context_map(
                    daily=daily,
                    cross_lookback_days=lb,
                    primary_days=cfg.strategy.primary_window_days,
                    secondary_days=cfg.strategy.secondary_window_days,
                    chop_lookback_days=cfg.strategy.chop_lookback_days,
                )
                for lb in cfg.strategy.cross_lookback_days
            }
            regime_by_flat = {
                ft: _build_monthly_regime_map(
                    daily=daily,
                    monthly=monthly,
                    flat_threshold=ft,
                )
                for ft in cfg.strategy.monthly_flat_thresholds
            }
            symbol_inputs[sym] = {
                "bars": bars,
                "daily": daily,
                "monthly": monthly,
                "context_by_lookback": context_by_lookback,
                "regime_by_flat": regime_by_flat,
            }

        if not symbol_inputs:
            raise ValueError("No symbols had enough 15m/daily history for EMA20 research")

        market_regime_map = _build_market_regime_from_benchmark(
            symbol_inputs=symbol_inputs,
            benchmark_symbol=str(cfg.strategy.market_regime_symbol),
            ema_period=int(cfg.strategy.market_regime_ema_period),
            enabled=bool(cfg.strategy.market_regime_filter_enabled),
        )
        if bool(cfg.strategy.market_regime_filter_enabled) and not market_regime_map:
            raise ValueError(
                f"market_regime_filter_enabled but no benchmark regime data for symbol={cfg.strategy.market_regime_symbol!r}"
            )

        variants = _build_variants(cfg.strategy)
        if config.variant_ids:
            requested = {str(v) for v in config.variant_ids if str(v).strip()}
            variants = [v for v in variants if v.variant_id in requested]
            if not variants:
                raise ValueError("Requested EMA20 variant_ids were not found in current config grid")
        all_trades: list[pd.DataFrame] = []
        for variant in variants:
            v_rows: list[dict] = []
            for sym, data in symbol_inputs.items():
                rows = _simulate_symbol_variant(
                    symbol=sym,
                    bars=data["bars"],
                    daily=data["daily"],
                    context_map=data["context_by_lookback"][variant.cross_lookback_days],
                    regime_map=data["regime_by_flat"][variant.flat_threshold],
                    market_regime_map=market_regime_map,
                    variant=variant,
                    strategy=cfg.strategy,
                    trade_start_date=config.start_date,
                )
                v_rows.extend(rows)
            vdf = pd.DataFrame(v_rows)
            if not vdf.empty:
                vdf = _apply_portfolio_constraints(vdf, variant)
            all_trades.append(vdf)

        trades = (
            pd.concat([df for df in all_trades if not df.empty], ignore_index=True)
            if any(not df.empty for df in all_trades)
            else pd.DataFrame(columns=_trade_columns())
        )
        buyhold_by_symbol, buyhold_summary = _build_buyhold_benchmark(
            symbol_inputs=symbol_inputs,
            start_date=config.start_date,
            end_date=config.end_date,
        )
        variant_vs_buyhold = _build_variant_vs_buyhold(trades=trades, buyhold_by_symbol=buyhold_by_symbol)

        metrics = _build_metrics(trades)
        metrics = _augment_metrics_with_buyhold(
            metrics=metrics,
            variant_vs_buyhold=variant_vs_buyhold,
            buyhold_equal_weight_return_pct=float(buyhold_summary.get("equal_weight_return_pct", 0.0) or 0.0),
        )

        summary = _write_outputs(
            run_id=run_id,
            cfg=cfg,
            config=config,
            symbols=list(symbol_inputs.keys()),
            data_sync_stats=data_sync_stats,
            variants=variants,
            trades=trades,
            metrics=metrics,
            buyhold_by_symbol=buyhold_by_symbol,
            variant_vs_buyhold=variant_vs_buyhold,
            buyhold_summary=buyhold_summary,
        )
        _persist_run(conn, run_id, variants, trades, metrics, summary)
        return summary
    except Exception as exc:
        conn.execute(
            """
            UPDATE ema20_strategy_runs
            SET completed_at=?, status='failed', summary_json=?
            WHERE run_id=?
            """,
            (
                datetime.now(CST).isoformat(),
                json.dumps({"error": str(exc)}, sort_keys=True),
                run_id,
            ),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def _load_15m_bars(
    conn: sqlite3.Connection,
    symbol: str,
    start_date: str,
    end_date: str,
    session_start: str,
    session_end: str,
) -> pd.DataFrame:
    q = """
    SELECT
      symbol, cst_date, open_ts_cst, close_ts_cst, open, high, low, close, volume
    FROM candles
    WHERE symbol = ?
      AND interval = '15m'
      AND cst_date >= ?
      AND cst_date <= ?
    ORDER BY open_ts_utc
    """
    bars = pd.read_sql_query(q, conn, params=(symbol, start_date, end_date))
    if bars.empty:
        return bars

    bars["open_ts"] = _parse_mixed_timestamps(bars["open_ts_cst"]).dt.tz_convert(CST)
    bars["close_ts"] = _parse_mixed_timestamps(bars["close_ts_cst"]).dt.tz_convert(CST)
    bars = bars.dropna(subset=["open_ts", "close_ts", "open", "high", "low", "close"]).copy()
    if bars.empty:
        return bars

    open_min, close_min = _session_minutes(session_start, session_end)
    mins = bars["open_ts"].dt.hour * 60 + bars["open_ts"].dt.minute
    bars = bars[(mins >= open_min) & (mins < close_min)].copy()
    if bars.empty:
        return bars

    bars = bars.sort_values("open_ts").reset_index(drop=True)
    bars["ema20_15m"] = bars["close"].astype(float).ewm(span=20, adjust=False).mean()
    return bars


def _parse_mixed_timestamps(series: pd.Series) -> pd.Series:
    # DB may contain mixed ISO layouts from different sources (e.g., "T" vs space separator).
    try:
        return pd.to_datetime(series, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return pd.to_datetime(series, utc=True, errors="coerce")


def _build_daily_from_15m(bars: pd.DataFrame, atr_period: int) -> pd.DataFrame:
    daily = (
        bars.groupby("cst_date", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .sort_values("cst_date")
        .reset_index(drop=True)
    )
    if daily.empty:
        return daily

    daily["date"] = pd.to_datetime(daily["cst_date"], errors="coerce")
    daily["ema20"] = daily["close"].astype(float).ewm(span=20, adjust=False).mean()
    prev_close = daily["close"].shift(1)
    tr = pd.concat(
        [
            (daily["high"] - daily["low"]).abs(),
            (daily["high"] - prev_close).abs(),
            (daily["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    daily["atr"] = tr.rolling(window=int(atr_period), min_periods=int(atr_period)).mean()
    return daily


def _build_monthly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    d["month"] = d["date"].dt.to_period("M")
    monthly = (
        d.groupby("month", as_index=False)
        .agg(
            month_open=("open", "first"),
            month_high=("high", "max"),
            month_low=("low", "min"),
            month_close=("close", "last"),
            month_end=("date", "max"),
        )
        .sort_values("month_end")
        .reset_index(drop=True)
    )
    if monthly.empty:
        return monthly
    monthly["ema20"] = monthly["month_close"].astype(float).ewm(span=20, adjust=False).mean()
    monthly["ema20_slope_pct"] = monthly["ema20"].pct_change().fillna(0.0)
    return monthly


def _latest_cross_index(hist: pd.DataFrame, lookback_days: int) -> int | None:
    if hist.empty:
        return None
    sub = hist.tail(int(lookback_days))
    cross_mask = (sub["low"] <= sub["ema20"]) & (sub["high"] >= sub["ema20"])
    cross_idx = sub.index[cross_mask].tolist()
    if not cross_idx:
        return None
    return int(cross_idx[-1])


def _build_day_context_map(
    daily: pd.DataFrame,
    cross_lookback_days: int,
    primary_days: int,
    secondary_days: int,
    chop_lookback_days: int,
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if daily.empty:
        return out

    for i in range(1, len(daily)):
        asof_date = str(daily.iloc[i]["cst_date"])
        hist = daily.iloc[:i].copy()
        if len(hist) < (secondary_days + 1):
            continue
        cross_idx = _latest_cross_index(hist, cross_lookback_days)
        if cross_idx is None or cross_idx < secondary_days:
            continue

        w1 = hist.iloc[cross_idx - primary_days : cross_idx]
        w2 = hist.iloc[cross_idx - secondary_days : cross_idx]
        if len(w1) < primary_days or len(w2) < secondary_days:
            continue

        chop_slice = hist.tail(int(max(1, chop_lookback_days)))
        chop_cross_count = int(((chop_slice["low"] <= chop_slice["ema20"]) & (chop_slice["high"] >= chop_slice["ema20"])).sum())

        out[asof_date] = {
            "cross_date": str(hist.iloc[cross_idx]["cst_date"]),
            "window_high_primary": float(w1["high"].max()),
            "window_low_primary": float(w1["low"].min()),
            "window_high_secondary": float(w2["high"].max()),
            "window_low_secondary": float(w2["low"].min()),
            "recent_cross_count": chop_cross_count,
        }
    return out


def _build_monthly_regime_map(
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    flat_threshold: float,
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if daily.empty or monthly.empty:
        return out

    m = monthly.copy()
    for _, row in daily.iterrows():
        cst_date = str(row["cst_date"])
        dt = pd.Timestamp(row["date"])
        month_start = dt.replace(day=1)
        eligible = m[m["month_end"] < month_start]
        if eligible.empty:
            out[cst_date] = {"regime": "FLAT", "slope_pct": 0.0, "month_end": ""}
            continue
        last = eligible.iloc[-1]
        slope_pct = float(last.get("ema20_slope_pct", 0.0) or 0.0)
        close = float(last["month_close"])
        ema20 = float(last["ema20"])

        if close > ema20 and slope_pct > float(flat_threshold):
            regime = "BULL"
        elif close < ema20 and slope_pct < -float(flat_threshold):
            regime = "BEAR"
        else:
            regime = "FLAT"

        out[cst_date] = {
            "regime": regime,
            "slope_pct": slope_pct,
            "month_end": str(pd.Timestamp(last["month_end"]).date()),
        }
    return out


def _build_variants(strategy: StrategyConfig) -> list[VariantSpec]:
    variants: list[VariantSpec] = []
    for lookback in strategy.cross_lookback_days:
        for flat in strategy.monthly_flat_thresholds:
            for entry in strategy.entry_variants:
                for chop_max in strategy.chop_cross_count_max_values:
                    for max_open in strategy.max_open_positions_values:
                        for max_new in strategy.max_new_entries_per_day_values:
                            suffix = f"__CH{int(chop_max)}__MO{int(max_open)}__ME{int(max_new)}"
                            for exit_v in strategy.exit_variants:
                                if exit_v == "X1":
                                    for n in strategy.daily_invalidation_days:
                                        variants.append(
                                            VariantSpec(
                                                variant_id=f"L{lookback}_F{flat:.4f}_{entry}_X1D{int(n)}{suffix}",
                                                cross_lookback_days=int(lookback),
                                                flat_threshold=float(flat),
                                                entry_variant=entry,
                                                exit_variant="X1",
                                                daily_invalidation_days=int(n),
                                                chop_cross_count_max=int(chop_max),
                                                max_open_positions=int(max_open),
                                                max_new_entries_per_day=int(max_new),
                                            )
                                        )
                                elif exit_v == "X2":
                                    for mult in strategy.atr_trail_multipliers:
                                        variants.append(
                                            VariantSpec(
                                                variant_id=f"L{lookback}_F{flat:.4f}_{entry}_X2ATR{float(mult):.2f}{suffix}",
                                                cross_lookback_days=int(lookback),
                                                flat_threshold=float(flat),
                                                entry_variant=entry,
                                                exit_variant="X2",
                                                atr_trail_mult=float(mult),
                                                chop_cross_count_max=int(chop_max),
                                                max_open_positions=int(max_open),
                                                max_new_entries_per_day=int(max_new),
                                            )
                                        )
                                elif exit_v == "X3":
                                    for days in strategy.time_stop_days:
                                        variants.append(
                                            VariantSpec(
                                                variant_id=f"L{lookback}_F{flat:.4f}_{entry}_X3T{int(days)}{suffix}",
                                                cross_lookback_days=int(lookback),
                                                flat_threshold=float(flat),
                                                entry_variant=entry,
                                                exit_variant="X3",
                                                time_stop_days=int(days),
                                                chop_cross_count_max=int(chop_max),
                                                max_open_positions=int(max_open),
                                                max_new_entries_per_day=int(max_new),
                                            )
                                        )
                                elif exit_v == "X4":
                                    variants.append(
                                        VariantSpec(
                                            variant_id=f"L{lookback}_F{flat:.4f}_{entry}_X4{suffix}",
                                            cross_lookback_days=int(lookback),
                                            flat_threshold=float(flat),
                                            entry_variant=entry,
                                            exit_variant="X4",
                                            chop_cross_count_max=int(chop_max),
                                            max_open_positions=int(max_open),
                                            max_new_entries_per_day=int(max_new),
                                        )
                                    )
    return variants


def _simulate_symbol_variant(
    symbol: str,
    bars: pd.DataFrame,
    daily: pd.DataFrame,
    context_map: dict[str, dict],
    regime_map: dict[str, dict],
    market_regime_map: dict[str, str],
    variant: VariantSpec,
    strategy: StrategyConfig,
    trade_start_date: str,
) -> list[dict]:
    if bars.empty:
        return []

    daily_dates = [str(x) for x in daily["cst_date"].tolist()]
    date_to_ord = {d: i for i, d in enumerate(daily_dates)}
    daily_by_date = {str(r["cst_date"]): r for _, r in daily.iterrows()}

    trades: list[dict] = []
    position: dict | None = None

    cycle_key: str | None = None
    cycle_entries_taken = 0
    post_exit_inrange_streak = 0
    last_exit_bar_idx = -10_000_000
    prev_raw_side = ""
    pending_retest: dict | None = None

    prev_date = ""

    def close_position(exit_price: float, exit_ts: str, exit_date: str, exit_reason: str, bar_idx: int) -> None:
        nonlocal position, last_exit_bar_idx, post_exit_inrange_streak
        if position is None:
            return

        side = str(position["side"])
        entry_price = float(position["entry_price"])
        init_risk = float(position["init_risk"])
        size_mult = float(position["size_mult"])
        roundtrip_cost = 2.0 * (float(strategy.transaction_cost_bps) / 10_000.0)
        if side == "LONG":
            net_pnl = (float(exit_price) - entry_price) - (entry_price * roundtrip_cost)
            raw_ret = net_pnl / entry_price
            r_mult = net_pnl / init_risk
        else:
            net_pnl = (entry_price - float(exit_price)) - (entry_price * roundtrip_cost)
            raw_ret = net_pnl / entry_price
            r_mult = net_pnl / init_risk

        entry_date = str(position["entry_date"])
        hold_days = 1
        if entry_date in date_to_ord and exit_date in date_to_ord:
            hold_days = max(1, int(date_to_ord[exit_date] - date_to_ord[entry_date] + 1))

        trades.append(
            {
                "variant_id": variant.variant_id,
                "symbol": symbol,
                "side": side,
                "entry_ts": str(position["entry_ts"]),
                "exit_ts": str(exit_ts),
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry_price": entry_price,
                "exit_price": float(exit_price),
                "stop_at_entry": float(position["stop_at_entry"]),
                "exit_reason": str(exit_reason),
                "hold_days": int(hold_days),
                "return_pct": float(raw_ret * 100.0),
                "weighted_return_pct": float(raw_ret * size_mult * 100.0),
                "r_mult": float(r_mult * size_mult),
                "size_mult": size_mult,
                "cross_date": str(position["cross_date"]),
                "cross_lookback_days": int(variant.cross_lookback_days),
                "flat_threshold": float(variant.flat_threshold),
                "entry_variant": str(variant.entry_variant),
                "exit_variant": str(variant.exit_variant),
            }
        )
        position = None
        last_exit_bar_idx = int(bar_idx)
        post_exit_inrange_streak = 0

    for i in range(len(bars)):
        row = bars.iloc[i]
        date = str(row["cst_date"])
        new_day = date != prev_date
        context = context_map.get(date)
        new_cycle_key = (str(context["cross_date"]) if context else None)

        if new_cycle_key != cycle_key:
            if position is not None:
                close_position(
                    exit_price=float(row["open"]),
                    exit_ts=str(row["open_ts"]),
                    exit_date=date,
                    exit_reason="CYCLE_RESET",
                    bar_idx=i,
                )
            cycle_key = new_cycle_key
            cycle_entries_taken = 0
            post_exit_inrange_streak = 0
            last_exit_bar_idx = -10_000_000
            prev_raw_side = ""
            pending_retest = None

        if position is not None and new_day:
            side = str(position["side"])
            curr_ord = date_to_ord.get(date)
            prev_ord = (curr_ord - 1) if curr_ord is not None else None
            prev_day_row = daily_by_date.get(daily_dates[prev_ord]) if prev_ord is not None and prev_ord >= 0 else None

            if variant.exit_variant == "X2" and prev_day_row is not None:
                atr = prev_day_row.get("atr")
                if atr is not None and not pd.isna(atr):
                    if side == "LONG":
                        new_stop = float(prev_day_row["close"]) - float(variant.atr_trail_mult or 2.0) * float(atr)
                        position["stop_price"] = max(float(position["stop_price"]), float(new_stop))
                    else:
                        new_stop = float(prev_day_row["close"]) + float(variant.atr_trail_mult or 2.0) * float(atr)
                        position["stop_price"] = min(float(position["stop_price"]), float(new_stop))

            if variant.exit_variant == "X1":
                n = int(variant.daily_invalidation_days or 1)
                if curr_ord is not None and curr_ord - n >= 0:
                    recent = daily.iloc[curr_ord - n : curr_ord]
                    if len(recent) == n:
                        if side == "LONG" and bool((recent["close"] < recent["ema20"]).all()):
                            close_position(float(row["open"]), str(row["open_ts"]), date, f"DAILY_EMA_INVALID_{n}", i)
                            prev_date = date
                            continue
                        if side == "SHORT" and bool((recent["close"] > recent["ema20"]).all()):
                            close_position(float(row["open"]), str(row["open_ts"]), date, f"DAILY_EMA_INVALID_{n}", i)
                            prev_date = date
                            continue

            if variant.exit_variant == "X3":
                max_days = int(variant.time_stop_days or 0)
                if max_days > 0 and date in date_to_ord and str(position["entry_date"]) in date_to_ord:
                    held = int(date_to_ord[date] - date_to_ord[str(position["entry_date"])] + 1)
                    if held >= max_days:
                        close_position(float(row["open"]), str(row["open_ts"]), date, f"TIME_STOP_{max_days}", i)
                        prev_date = date
                        continue

            if variant.exit_variant == "X4":
                regime = str(regime_map.get(date, {}).get("regime", "FLAT"))
                if side == "LONG" and regime == "BEAR":
                    close_position(float(row["open"]), str(row["open_ts"]), date, "MONTHLY_FLIP", i)
                    prev_date = date
                    continue
                if side == "SHORT" and regime == "BULL":
                    close_position(float(row["open"]), str(row["open_ts"]), date, "MONTHLY_FLIP", i)
                    prev_date = date
                    continue

        if position is not None:
            side = str(position["side"])
            stop_price = float(position["stop_price"])
            if side == "LONG" and float(row["low"]) <= stop_price:
                stop_fill = min(stop_price, float(row["open"]))
                close_position(stop_fill, str(row["close_ts"]), date, "STOP", i)
                prev_date = date
                continue
            if side == "SHORT" and float(row["high"]) >= stop_price:
                stop_fill = max(stop_price, float(row["open"]))
                close_position(stop_fill, str(row["close_ts"]), date, "STOP", i)
                prev_date = date
                continue

        if position is None and context is not None:
            wl1 = float(context["window_low_primary"])
            wh1 = float(context["window_high_primary"])
            wl2 = float(context["window_low_secondary"])
            wh2 = float(context["window_high_secondary"])

            if bool(strategy.chop_filter_enabled):
                recent_cross_count = int(context.get("recent_cross_count", 0) or 0)
                if recent_cross_count > int(variant.chop_cross_count_max):
                    prev_date = date
                    continue

            if cycle_entries_taken > 0:
                in_range = wl1 <= float(row["close"]) <= wh1
                post_exit_inrange_streak = (post_exit_inrange_streak + 1) if in_range else 0

            total_allowed = 1 + int(strategy.max_reentries_per_cycle)
            if cycle_entries_taken == 0:
                entry_allowed = True
            elif not bool(strategy.allow_reentry):
                entry_allowed = False
            else:
                entry_allowed = (
                    cycle_entries_taken < total_allowed
                    and (i - last_exit_bar_idx) >= int(strategy.reentry_cooldown_bars)
                    and post_exit_inrange_streak >= int(strategy.reentry_reset_bars)
                )
            if not entry_allowed:
                prev_date = date
                continue

            if date < str(trade_start_date):
                prev_date = date
                continue

            long_trigger_level = max(wh1, wh2) if bool(strategy.require_secondary_window_confirm) else wh1
            short_trigger_level = min(wl1, wl2) if bool(strategy.require_secondary_window_confirm) else wl1
            raw_long = (float(row["close"]) > long_trigger_level) and (float(row["close"]) > float(row["ema20_15m"]))
            raw_short = (float(row["close"]) < short_trigger_level) and (float(row["close"]) < float(row["ema20_15m"]))
            raw_side = "LONG" if raw_long else ("SHORT" if raw_short else "")

            side_mode = str(getattr(strategy, "side_mode", "both")).strip().lower()
            if side_mode == "long_only" and raw_side == "SHORT":
                raw_side = ""
            elif side_mode == "short_only" and raw_side == "LONG":
                raw_side = ""

            trigger_side = ""
            entry_variant = str(variant.entry_variant).upper()
            if entry_variant == "E1":
                trigger_side = raw_side
            elif entry_variant == "E2":
                if raw_side and raw_side == prev_raw_side:
                    trigger_side = raw_side
            elif entry_variant == "E3":
                if pending_retest is not None and i > int(pending_retest["expiry_idx"]):
                    pending_retest = None
                if pending_retest is not None:
                    p_side = str(pending_retest["side"])
                    if p_side == "LONG":
                        if float(row["low"]) <= float(pending_retest["boundary"]):
                            pending_retest["touched"] = True
                        if bool(pending_retest["touched"]) and raw_long:
                            trigger_side = "LONG"
                            pending_retest = None
                    elif p_side == "SHORT":
                        if float(row["high"]) >= float(pending_retest["boundary"]):
                            pending_retest["touched"] = True
                        if bool(pending_retest["touched"]) and raw_short:
                            trigger_side = "SHORT"
                            pending_retest = None
                if trigger_side == "" and pending_retest is None:
                    if raw_long:
                        pending_retest = {
                            "side": "LONG",
                            "boundary": float(wh1),
                            "touched": False,
                            "expiry_idx": int(i + int(strategy.retest_max_bars)),
                        }
                    elif raw_short:
                        pending_retest = {
                            "side": "SHORT",
                            "boundary": float(wl1),
                            "touched": False,
                            "expiry_idx": int(i + int(strategy.retest_max_bars)),
                        }
            else:
                trigger_side = raw_side

            prev_raw_side = raw_side
            if not trigger_side:
                prev_date = date
                continue

            if bool(getattr(strategy, "market_regime_filter_enabled", False)):
                mreg = str(market_regime_map.get(date, ""))
                if trigger_side == "LONG" and mreg != "BULL":
                    prev_date = date
                    continue
                if trigger_side == "SHORT" and mreg != "BEAR":
                    prev_date = date
                    continue

            if bool(strategy.require_daily_ema_side):
                curr_ord = date_to_ord.get(date)
                prev_row = (
                    daily.iloc[curr_ord - 1]
                    if curr_ord is not None and curr_ord > 0
                    else None
                )
                if prev_row is None:
                    prev_date = date
                    continue
                prev_close = float(prev_row["close"])
                prev_ema20 = float(prev_row["ema20"])
                if trigger_side == "LONG" and not (prev_close > prev_ema20):
                    prev_date = date
                    continue
                if trigger_side == "SHORT" and not (prev_close < prev_ema20):
                    prev_date = date
                    continue

            regime = str(regime_map.get(date, {}).get("regime", "FLAT"))
            blocked, size_mult = _regime_gate_and_size(trigger_side, regime, strategy)
            if blocked:
                prev_date = date
                continue

            if (i + 1) >= len(bars):
                prev_date = date
                continue
            next_bar = bars.iloc[i + 1]
            entry_price = float(next_bar["open"])
            entry_ts = str(next_bar["open_ts"])
            entry_date = str(next_bar["cst_date"])

            if trigger_side == "LONG":
                stop_at_entry = float(wl2)
                if stop_at_entry >= entry_price:
                    stop_at_entry = float(entry_price * 0.99)
                init_risk = float(entry_price - stop_at_entry)
            else:
                stop_at_entry = float(wh2)
                if stop_at_entry <= entry_price:
                    stop_at_entry = float(entry_price * 1.01)
                init_risk = float(stop_at_entry - entry_price)
            if init_risk <= 0:
                prev_date = date
                continue

            position = {
                "side": trigger_side,
                "entry_ts": entry_ts,
                "entry_price": entry_price,
                "entry_date": entry_date,
                "cross_date": str(context["cross_date"]),
                "stop_price": float(stop_at_entry),
                "stop_at_entry": float(stop_at_entry),
                "init_risk": float(init_risk),
                "size_mult": float(size_mult),
            }
            cycle_entries_taken += 1
            post_exit_inrange_streak = 0
            pending_retest = None

        prev_date = date

    if position is not None and not bars.empty:
        last = bars.iloc[-1]
        close_position(
            exit_price=float(last["close"]),
            exit_ts=str(last["close_ts"]),
            exit_date=str(last["cst_date"]),
            exit_reason="END_OF_TEST",
            bar_idx=len(bars),
        )

    return trades


def _build_metrics(trades: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant_id",
        "trades_count",
        "win_rate",
        "avg_return_pct",
        "avg_weighted_return_pct",
        "total_return_pct",
        "profit_factor",
        "max_drawdown_pct",
        "avg_hold_days",
        "long_trades",
        "short_trades",
    ]
    if trades.empty:
        return pd.DataFrame(columns=cols)

    rows: list[dict] = []
    for variant_id, grp in trades.groupby("variant_id", sort=True):
        g = grp.sort_values("exit_ts").copy()
        r = g["weighted_return_pct"].astype(float) / 100.0
        equity = (1.0 + r).cumprod()
        dd = equity / equity.cummax() - 1.0

        gp = float(r[r > 0].sum())
        gl = abs(float(r[r < 0].sum()))
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)

        rows.append(
            {
                "variant_id": str(variant_id),
                "trades_count": int(len(g)),
                "win_rate": float((r > 0).mean()),
                "avg_return_pct": float(g["return_pct"].astype(float).mean()),
                "avg_weighted_return_pct": float(g["weighted_return_pct"].astype(float).mean()),
                "total_return_pct": float((equity.iloc[-1] - 1.0) * 100.0) if len(equity) else 0.0,
                "profit_factor": float(pf),
                "max_drawdown_pct": float(abs(dd.min()) * 100.0) if len(dd) else 0.0,
                "avg_hold_days": float(g["hold_days"].astype(float).mean()),
                "long_trades": int((g["side"] == "LONG").sum()),
                "short_trades": int((g["side"] == "SHORT").sum()),
            }
        )
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["total_return_pct", "avg_weighted_return_pct"],
        ascending=[False, False],
    )


def _build_buyhold_benchmark(
    symbol_inputs: dict[str, dict],
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, dict]:
    rows: list[dict] = []
    for symbol, data in symbol_inputs.items():
        daily = data.get("daily")
        if daily is None or daily.empty:
            continue
        d = daily[(daily["cst_date"] >= start_date) & (daily["cst_date"] <= end_date)].copy()
        if d.empty:
            continue
        d = d.sort_values("cst_date").reset_index(drop=True)
        start_close = float(d.iloc[0]["close"])
        end_close = float(d.iloc[-1]["close"])
        buyhold_return_pct = 0.0
        if start_close > 0:
            buyhold_return_pct = float((end_close / start_close - 1.0) * 100.0)
        rows.append(
            {
                "symbol": str(symbol),
                "start_date_used": str(d.iloc[0]["cst_date"]),
                "end_date_used": str(d.iloc[-1]["cst_date"]),
                "start_close": start_close,
                "end_close": end_close,
                "buyhold_return_pct": buyhold_return_pct,
            }
        )

    df = pd.DataFrame(
        rows,
        columns=["symbol", "start_date_used", "end_date_used", "start_close", "end_close", "buyhold_return_pct"],
    )
    if df.empty:
        return df, {"symbols_count": 0, "equal_weight_return_pct": 0.0, "median_return_pct": 0.0}

    eq = float(df["buyhold_return_pct"].astype(float).mean())
    med = float(df["buyhold_return_pct"].astype(float).median())
    top = df.sort_values("buyhold_return_pct", ascending=False).iloc[0].to_dict()
    bot = df.sort_values("buyhold_return_pct", ascending=True).iloc[0].to_dict()
    summary = {
        "symbols_count": int(len(df)),
        "equal_weight_return_pct": eq,
        "median_return_pct": med,
        "best_symbol": {
            "symbol": str(top.get("symbol", "")),
            "buyhold_return_pct": float(top.get("buyhold_return_pct", 0.0) or 0.0),
        },
        "worst_symbol": {
            "symbol": str(bot.get("symbol", "")),
            "buyhold_return_pct": float(bot.get("buyhold_return_pct", 0.0) or 0.0),
        },
    }
    return df, summary


def _build_market_regime_from_benchmark(
    symbol_inputs: dict[str, dict],
    benchmark_symbol: str,
    ema_period: int,
    enabled: bool,
) -> dict[str, str]:
    if not enabled:
        return {}
    sym = str(benchmark_symbol).strip().upper()
    data = symbol_inputs.get(sym)
    if not data:
        return {}
    daily = data.get("daily")
    if daily is None or daily.empty:
        return {}

    d = daily.copy().sort_values("cst_date").reset_index(drop=True)
    p = int(max(2, ema_period))
    d["ema_ref"] = d["close"].astype(float).ewm(span=p, adjust=False).mean()

    out: dict[str, str] = {}
    for i in range(1, len(d)):
        asof_date = str(d.iloc[i]["cst_date"])
        prev_close = float(d.iloc[i - 1]["close"])
        prev_ema = float(d.iloc[i - 1]["ema_ref"])
        if prev_close > prev_ema:
            out[asof_date] = "BULL"
        elif prev_close < prev_ema:
            out[asof_date] = "BEAR"
        else:
            out[asof_date] = "FLAT"
    return out


def _build_variant_vs_buyhold(trades: pd.DataFrame, buyhold_by_symbol: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant_id",
        "symbol",
        "trades_count",
        "strategy_return_pct",
        "buyhold_return_pct",
        "excess_vs_buyhold_pct",
    ]
    if trades.empty or buyhold_by_symbol.empty:
        return pd.DataFrame(columns=cols)

    bh_map = {
        str(r["symbol"]): float(r["buyhold_return_pct"])
        for _, r in buyhold_by_symbol.iterrows()
    }
    rows: list[dict] = []
    for (variant_id, symbol), grp in trades.groupby(["variant_id", "symbol"], sort=True):
        g = grp.sort_values("exit_ts").copy()
        r = g["weighted_return_pct"].astype(float) / 100.0
        strategy_return_pct = float(((1.0 + r).prod() - 1.0) * 100.0) if len(r) else 0.0
        buyhold_return_pct = float(bh_map.get(str(symbol), 0.0))
        rows.append(
            {
                "variant_id": str(variant_id),
                "symbol": str(symbol),
                "trades_count": int(len(g)),
                "strategy_return_pct": strategy_return_pct,
                "buyhold_return_pct": buyhold_return_pct,
                "excess_vs_buyhold_pct": float(strategy_return_pct - buyhold_return_pct),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def _augment_metrics_with_buyhold(
    metrics: pd.DataFrame,
    variant_vs_buyhold: pd.DataFrame,
    buyhold_equal_weight_return_pct: float,
) -> pd.DataFrame:
    if metrics.empty:
        out = metrics.copy()
        out["buyhold_equal_weight_return_pct"] = float(buyhold_equal_weight_return_pct)
        out["excess_vs_buyhold_equal_weight_pct"] = 0.0
        out["avg_symbol_excess_vs_buyhold_pct"] = 0.0
        out["symbols_beating_buyhold_count"] = 0
        return out

    out = metrics.copy()
    out["buyhold_equal_weight_return_pct"] = float(buyhold_equal_weight_return_pct)
    out["excess_vs_buyhold_equal_weight_pct"] = out["total_return_pct"].astype(float) - float(buyhold_equal_weight_return_pct)

    if variant_vs_buyhold.empty:
        out["avg_symbol_excess_vs_buyhold_pct"] = 0.0
        out["symbols_beating_buyhold_count"] = 0
        return out.sort_values(
            ["excess_vs_buyhold_equal_weight_pct", "total_return_pct", "avg_weighted_return_pct"],
            ascending=[False, False, False],
        )

    agg = (
        variant_vs_buyhold.groupby("variant_id", as_index=False)
        .agg(
            avg_symbol_excess_vs_buyhold_pct=("excess_vs_buyhold_pct", "mean"),
            symbols_beating_buyhold_count=("excess_vs_buyhold_pct", lambda s: int((s > 0).sum())),
        )
    )
    out = out.merge(agg, on="variant_id", how="left")
    out["avg_symbol_excess_vs_buyhold_pct"] = out["avg_symbol_excess_vs_buyhold_pct"].fillna(0.0)
    out["symbols_beating_buyhold_count"] = out["symbols_beating_buyhold_count"].fillna(0).astype(int)
    return out.sort_values(
        [
            "excess_vs_buyhold_equal_weight_pct",
            "avg_symbol_excess_vs_buyhold_pct",
            "total_return_pct",
            "avg_weighted_return_pct",
        ],
        ascending=[False, False, False, False],
    )


def _regime_gate_and_size(side: str, regime: str, strategy: StrategyConfig) -> tuple[bool, float]:
    opp_mode = str(getattr(strategy, "monthly_opposite_mode", "block")).lower().strip()
    flat_mode = str(getattr(strategy, "monthly_flat_mode", "reduce")).lower().strip()
    reduce_mult = float(getattr(strategy, "flat_size_multiplier", 0.50))

    opposite = (side == "LONG" and regime == "BEAR") or (side == "SHORT" and regime == "BULL")
    if opposite:
        if opp_mode == "block":
            return True, 0.0
        if opp_mode == "reduce":
            return False, reduce_mult
        return False, 1.0

    if regime == "FLAT":
        if flat_mode == "block":
            return True, 0.0
        if flat_mode == "reduce":
            return False, reduce_mult
        return False, 1.0

    return False, 1.0


def _apply_portfolio_constraints(trades: pd.DataFrame, variant: VariantSpec) -> pd.DataFrame:
    if trades.empty:
        return trades

    work = trades.copy()
    work["entry_dt"] = pd.to_datetime(work["entry_ts"], errors="coerce", utc=True)
    work["exit_dt"] = pd.to_datetime(work["exit_ts"], errors="coerce", utc=True)
    work["entry_day"] = work["entry_date"].astype(str)
    work = work.dropna(subset=["entry_dt", "exit_dt"]).copy()
    if work.empty:
        return work

    work = work.sort_values(["entry_dt", "symbol", "entry_price"], ascending=[True, True, True]).reset_index(drop=True)

    accepted: list[int] = []
    active_exit_times: list[pd.Timestamp] = []
    entries_per_day: dict[str, int] = {}

    for idx, row in work.iterrows():
        entry_dt = row["entry_dt"]
        exit_dt = row["exit_dt"]
        entry_day = str(row["entry_day"])

        active_exit_times = [t for t in active_exit_times if t > entry_dt]

        if entries_per_day.get(entry_day, 0) >= int(variant.max_new_entries_per_day):
            continue
        if len(active_exit_times) >= int(variant.max_open_positions):
            continue

        accepted.append(int(idx))
        entries_per_day[entry_day] = int(entries_per_day.get(entry_day, 0) + 1)
        active_exit_times.append(exit_dt)

    kept = work.iloc[accepted].drop(columns=["entry_dt", "exit_dt", "entry_day"]).reset_index(drop=True)
    return kept


def _persist_run(
    conn: sqlite3.Connection,
    run_id: str,
    variants: list[VariantSpec],
    trades: pd.DataFrame,
    metrics: pd.DataFrame,
    summary: dict,
) -> None:
    conn.execute("DELETE FROM ema20_trades WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM ema20_metrics WHERE run_id=?", (run_id,))

    if not trades.empty:
        t = trades.copy()
        t.insert(0, "run_id", run_id)
        t.to_sql("ema20_trades", conn, if_exists="append", index=False)
    if not metrics.empty:
        base_cols = [
            "variant_id",
            "trades_count",
            "win_rate",
            "avg_return_pct",
            "avg_weighted_return_pct",
            "total_return_pct",
            "profit_factor",
            "max_drawdown_pct",
            "avg_hold_days",
            "long_trades",
            "short_trades",
        ]
        keep = [c for c in base_cols if c in metrics.columns]
        m = metrics[keep].copy()
        m.insert(0, "run_id", run_id)
        m.to_sql("ema20_metrics", conn, if_exists="append", index=False)

    conn.execute(
        """
        UPDATE ema20_strategy_runs
        SET completed_at=?, status='completed', variant_count=?, trades_count=?, summary_json=?
        WHERE run_id=?
        """,
        (
            datetime.now(CST).isoformat(),
            int(len(variants)),
            int(len(trades)),
            json.dumps(summary, sort_keys=True),
            run_id,
        ),
    )
    conn.commit()


def _write_outputs(
    run_id: str,
    cfg: AppConfig,
    config: ResearchConfig,
    symbols: list[str],
    data_sync_stats: dict,
    variants: list[VariantSpec],
    trades: pd.DataFrame,
    metrics: pd.DataFrame,
    buyhold_by_symbol: pd.DataFrame,
    variant_vs_buyhold: pd.DataFrame,
    buyhold_summary: dict,
) -> dict:
    out_dir = _resolve_project_path(cfg.research_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(CST).strftime("%Y%m%d_%H%M%S")

    trades_path = out_dir / f"ema20_trades_{ts}.csv"
    metrics_path = out_dir / f"ema20_variant_metrics_{ts}.csv"
    buyhold_path = out_dir / f"ema20_buyhold_symbols_{ts}.csv"
    variant_vs_buyhold_path = out_dir / f"ema20_variant_vs_buyhold_{ts}.csv"
    summary_path = out_dir / f"ema20_summary_{ts}.json"

    trades.to_csv(trades_path, index=False)
    metrics.to_csv(metrics_path, index=False)
    buyhold_by_symbol.to_csv(buyhold_path, index=False)
    variant_vs_buyhold.to_csv(variant_vs_buyhold_path, index=False)

    best = metrics.iloc[0].to_dict() if not metrics.empty else None
    summary = {
        "run_id": run_id,
        "status": "completed",
        "start_date": config.start_date,
        "end_date": config.end_date,
        "symbols": symbols,
        "variants_tested": int(len(variants)),
        "trades_count": int(len(trades)),
        "best_variant": best,
        "buyhold_benchmark": buyhold_summary,
        "data_sync_stats": data_sync_stats,
        "artifacts": {
            "trades": str(trades_path).replace("\\", "/"),
            "metrics": str(metrics_path).replace("\\", "/"),
            "buyhold_symbols": str(buyhold_path).replace("\\", "/"),
            "variant_vs_buyhold": str(variant_vs_buyhold_path).replace("\\", "/"),
            "summary": str(summary_path).replace("\\", "/"),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _resolve_project_path(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / p).resolve()


def _history_start_date(start_date: str) -> str:
    # Warmup for daily cross windows + monthly EMA20 context.
    start = date.fromisoformat(start_date)
    warmup = start - timedelta(days=750)
    return warmup.isoformat()


def _session_minutes(session_start: str, session_end: str) -> tuple[int, int]:
    sh, sm = [int(x) for x in session_start.split(":")]
    eh, em = [int(x) for x in session_end.split(":")]
    return sh * 60 + sm, eh * 60 + em


def _trade_columns() -> list[str]:
    return [
        "variant_id",
        "symbol",
        "side",
        "entry_ts",
        "exit_ts",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "stop_at_entry",
        "exit_reason",
        "hold_days",
        "return_pct",
        "weighted_return_pct",
        "r_mult",
        "size_mult",
        "cross_date",
        "cross_lookback_days",
        "flat_threshold",
        "entry_variant",
        "exit_variant",
    ]
