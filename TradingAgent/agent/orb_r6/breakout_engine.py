from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional, List, Tuple

import pandas as pd

from .notifier import render_alert, send_discord
from .breakouts import (
    RRRow,
    HorizonState,
    evaluate_close_only_breakouts,
    insert_breakout_event,
    insert_event_horizon_metrics,
)
from .quality_metrics import compute_quality
from .decision_engine import decide, reasons_to_json, ENGINE_VERSION
from .labels import label_overlap, label_inflation, label_bias, label_quality


@dataclass
class EngineBarResult:
    """Result of evaluating a single bar."""
    event_id: Optional[str]
    primary_horizon: Optional[int]
    direction: Optional[str]
    include_today_or: int
    content: Optional[str]


def load_day_candles(conn: sqlite3.Connection, symbol: str, interval: str, cst_date: str) -> pd.DataFrame:
    """Load a single session day's candles from DB."""
    q = """
    SELECT open_ts_cst, close_ts_cst, open, high, low, close
    FROM candles
    WHERE symbol=? AND interval=? AND cst_date=?
    ORDER BY open_ts_cst
    """
    df = pd.read_sql_query(q, conn, params=[symbol, interval, cst_date])
    return df


def evaluate_bar_close_only(
    conn: sqlite3.Connection,
    *,
    templates: dict,
    discord_enabled: bool,
    webhook: str,
    tag: str,
    mode: str,
    symbol: str,
    asof_date_cst: str,
    interval: str,
    or_minutes: int,
    horizons: List[int],
    rr_pre: Dict[int, RRRow],
    rr_post: Dict[int, RRRow],
    rr_seed: Dict[int, RRRow],
    state_by_h: Dict[int, HorizonState],
    row: pd.Series,
    or_end_ts_cst: pd.Timestamp,
) -> EngineBarResult:
    """Evaluate one bar for breakouts (close-only), insert event rows, and optionally send discord."""

    close_price = float(row["close"])
    bar_open_ts_cst = str(row["open_ts_cst"])
    bar_close_ts_cst = str(row["close_ts_cst"])

    bar_close_dt = pd.to_datetime(bar_close_ts_cst)
    include_today_or = 0 if (bar_close_dt < or_end_ts_cst) else 1

    rr_active = rr_pre if (include_today_or == 0 and rr_pre) else (rr_post if rr_post else rr_pre)
    if not rr_active:
        return EngineBarResult(None, None, None, include_today_or, None)

    primary, broke = evaluate_close_only_breakouts(close_price, rr_active, state_by_h, horizons)
    if primary is None:
        return EngineBarResult(None, None, None, include_today_or, None)

    direction = broke[primary]["direction"]
    breakout_strength = float(broke[primary]["breakout_strength"])
    also = [h for h in horizons if h in broke and h != primary]
    also_str = ", ".join([f"H{h}" for h in also]) if also else "None"

    rr = rr_active[primary]

    # Quality metrics per horizon (computed only for horizons that broke)
    quality_by_h: Dict[int, dict] = {}
    for h, rrh in rr_active.items():
        dd = broke.get(h, {}).get("direction")
        if not dd:
            continue
        quality_by_h[h] = compute_quality(
            direction=dd,
            ref_low=rrh.ref_low,
            ref_high=rrh.ref_high,
            ref_width=rrh.ref_width,
            o=float(row["open"]),
            h=float(row["high"]),
            l=float(row["low"]),
            c=float(row["close"]),
        )
    q_primary = quality_by_h.get(primary, {"close_pen": 0.0, "wick_pen": 0.0, "body_norm": 0.0, "range_norm": 0.0})

    # Labels
    overlap_lbl = label_overlap(rr.or_overlap_pairs_pct, rr.or_overlap_days_pct)
    infl_lbl = label_inflation(rr.inflation_factor)
    bias_lbl = label_bias(rr.mean_direction_bias, rr.bias_consistency)
    strength_lbl, wick_lbl, body_lbl = label_quality(breakout_strength, q_primary.get("wick_pen"), q_primary.get("body_norm"))

    rr_phase = "pre-OR" if include_today_or == 0 else "post-OR"
    rr_phase_emoji = "🧊" if include_today_or == 0 else "🔥"
    mode_emoji = "🟣" if mode.upper() == "REPLAY" else "🟢"
    dir_emoji = "🔺" if direction == "UP" else "🔻"

    # Decision engine input features
    features = {
        "direction": direction,
        "breakout_strength": breakout_strength,
        "inflation_factor": rr.inflation_factor,
        "or_overlap_pairs_pct": rr.or_overlap_pairs_pct,
        "mean_direction_bias": rr.mean_direction_bias,
        "bias_consistency": rr.bias_consistency,
        "wick_pen": q_primary.get("wick_pen"),
        "close_pen": q_primary.get("close_pen"),
        "also_count": len(also),
        "include_today_or": include_today_or,
    }
    dec = decide(features)
    reasons_json = reasons_to_json(dec.reasons)

    decision_emoji = "✅" if dec.decision in ("LONG", "SHORT") else "⛔"
    confidence_pct = int(round(dec.confidence * 100))
    decision_story = f"{infl_lbl.text}, {overlap_lbl.text.lower()}, {strength_lbl.text.lower()}"
    reasons_short = ", ".join(dec.reasons[:4]) if dec.reasons else ""

    payload = {
        "tag": tag,
        "symbol": symbol,
        "direction": direction,
        "dir_emoji": dir_emoji,
        "primary_horizon": primary,
        "also_horizons": also_str,
        "mode": mode.upper(),
        "mode_emoji": mode_emoji,
        "rr_phase": rr_phase,
        "rr_phase_emoji": rr_phase_emoji,
        "decision": dec.decision,
        "confidence_pct": confidence_pct,
        "decision_emoji": decision_emoji,
        "decision_story": decision_story,
        "reasons_short": reasons_short,
        "ref_low": rr.ref_low,
        "ref_high": rr.ref_high,
        "ref_width": rr.ref_width,
        "inflation_factor": float(rr.inflation_factor or 0.0),
        "overlap_emoji": overlap_lbl.emoji,
        "overlap_label": overlap_lbl.text,
        "infl_emoji": infl_lbl.emoji,
        "infl_label": infl_lbl.text,
        "bias_emoji": bias_lbl.emoji,
        "bias_label": bias_lbl.text,
        "strength_emoji": strength_lbl.emoji,
        "strength_label": strength_lbl.text,
        "wick_emoji": wick_lbl.emoji,
        "body_emoji": body_lbl.emoji,
        "close_pen_pct": float(q_primary.get("close_pen", 0.0) * 100.0),
        "wick_pen_pct": float(q_primary.get("wick_pen", 0.0) * 100.0),
        "body_norm_pct": float(q_primary.get("body_norm", 0.0) * 100.0),
        "range_norm_pct": float(q_primary.get("range_norm", 0.0) * 100.0),
        "close_pos_pct": float(q_primary.get("close_pos", 0.0) * 100.0),
        "upper_wick_pct": float(q_primary.get("upper_wick_ratio", 0.0) * 100.0),
        "lower_wick_pct": float(q_primary.get("lower_wick_ratio", 0.0) * 100.0),
        "clean_break": int(q_primary.get("clean_break", 0.0)),
        "clean_emoji": ("✅" if int(q_primary.get("clean_break", 0.0)) == 1 else "⚠️"),
        "clean_label": ("clean" if int(q_primary.get("clean_break", 0.0)) == 1 else "not clean"),
        "breakout_strength": breakout_strength,
        "bar_close_ts_cst": bar_close_ts_cst,
    }

    content = render_alert(templates, payload, template_key="breakout_default")

    event_id, inserted_new = insert_breakout_event(
        conn=conn,
        symbol=symbol,
        asof_date_cst=asof_date_cst,
        bar_open_ts_cst=bar_open_ts_cst,
        bar_close_ts_cst=bar_close_ts_cst,
        interval=interval,
        orb_minutes=or_minutes,
        direction=direction,
        primary_horizon=primary,
        include_today_or=include_today_or,
        trigger_type="close_only",
        ref_low=rr.ref_low,
        ref_high=rr.ref_high,
        ref_width=rr.ref_width,
        candle_open=float(row["open"]),
        candle_high=float(row["high"]),
        candle_low=float(row["low"]),
        candle_close=float(row["close"]),
        decision=dec.decision,
        confidence=float(dec.confidence),
        reasons_json=reasons_json,
        engine_version=ENGINE_VERSION,
        message=content,
    )
    insert_event_horizon_metrics(conn, event_id, rr_active, broke, primary, quality_by_h=quality_by_h)

    if inserted_new and discord_enabled and webhook:
        ok = send_discord(webhook, content)
        if ok:
            conn.execute("UPDATE breakout_events SET sent_to_discord=1 WHERE event_id=?", [event_id])
            conn.commit()

    return EngineBarResult(event_id, primary, direction, include_today_or, content)
