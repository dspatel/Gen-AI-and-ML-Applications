from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, List

import pandas as pd

from src.config_loader import load_yaml, load_symbols
from src.calendar import TradingCalendar
from src.db import DB
from src.rr_compute import compute_reference_ranges
from src.data_fetch import fetch_intraday_yfinance
from src.breakout_engine import BreakoutParams, init_states, evaluate_bar_multi_horizon, choose_primary_event
from src.notify_discord import discord_config_from_cfg, send_discord_message
from src.notifications import load_templates, render_message
from src.label_engine import LabelEngine
from src.decision import compute_trade_decision
import yaml


def _report_dir(cfg: Dict[str, Any]) -> str:
    mode = str(cfg.get("run", {}).get("mode", "TEST")).upper()
    rep = cfg.get("reporting", {}) or {}
    return rep.get("output_dir_prod") if mode == "PROD" else rep.get("output_dir_test")


def _db_path(cfg: Dict[str, Any]) -> str:
    mode = str(cfg.get("run", {}).get("mode", "TEST")).upper()
    st = cfg.get("storage", {}) or {}
    return st.get("db_path_prod") if mode == "PROD" else st.get("db_path_test")


def format_message(symbol: str, asof: str, primary: Dict[str, Any], also: List[int], rr_by_h: Dict[int, Dict[str, Any]], is_replay: bool) -> str:
    direction = primary["direction"]
    emoji = "🟢" if direction == "UP" else "🔴"
    ts = primary["timestamp"]
    h = int(primary["horizon_days"])
    strength = float(primary["breakout_strength"])
    amt = float(primary["breakout_amt"])
    ref_high = float(primary["ref_high"])
    ref_low = float(primary["ref_low"])
    ref_w = float(primary["ref_width"])

    also_txt = (", ".join(str(x) for x in also)) if also else "None"
    tag = " [REPLAY]" if is_replay else ""
    # Add a couple quick intuition bits from the primary horizon RR metrics
    rr = rr_by_h.get(h, {})
    overlap = rr.get("or_overlap_pairs_pct", None)
    inside = rr.get("median_inside_own_or_pct", None)

    parts = [
        f"{emoji} {symbol} {direction}{tag} — {asof} — Primary {h}D",
        f"Close={primary['close']:.2f} | RR=[{ref_low:.2f}, {ref_high:.2f}] (W={ref_w:.2f})",
        f"Intensity: {amt:.2f} ({strength:.2f}R) | Also broke: {also_txt}",
    ]
    if overlap is not None or inside is not None:
        bits = []
        if overlap is not None:
            bits.append(f"OR overlap={float(overlap)*100:.0f}%")
        if inside is not None:
            bits.append(f"Median inside-own-OR={float(inside)*100:.0f}%")
        if bits:
            parts.append("Lookback: " + " | ".join(bits))
    return "\n".join(parts)



def _load_label_engine(cfg: Dict[str, Any]) -> LabelEngine:
    path = str(cfg.get("interpretation", {}).get("labels_path", "config/labels.yml"))
    with open(path, "r", encoding="utf-8") as f:
        labels_cfg = yaml.safe_load(f) or {}
    return LabelEngine(labels_cfg)

def _load_templates(cfg: Dict[str, Any]) -> Dict[str, Any]:
    path = str(cfg.get("notifications", {}).get("templates_path", "config/notification_templates.yml"))
    return load_templates(path)

def build_message(
    cfg: Dict[str, Any],
    symbol: str,
    asof: str,
    primary: Dict[str, Any],
    also: List[int],
    rr_by_h: Dict[int, Dict[str, Any]],
    is_replay: bool,
) -> str:
    templates = _load_templates(cfg)
    le = _load_label_engine(cfg)
    rules_path = str(cfg.get("interpretation", {}).get("decision_rules_path", "config/decision_rules.yml"))
    rules_cfg = yaml.safe_load(open(rules_path, "r", encoding="utf-8")) or {}

    h = int(primary["horizon_days"])
    rr = rr_by_h.get(h, {}) or {}
    labels = le.labels_for(metrics={
        "or_overlap_ratio": rr.get("or_overlap_ratio", rr.get("or_overlap_pairs_pct", 0.0)),
        "inflation_factor": rr.get("inflation_factor", None),
        "mean_direction_bias": rr.get("mean_direction_bias", None),
        "breakout_strength": primary.get("breakout_strength", None),
    }, direction=str(primary.get("direction")))

    decision_res = compute_trade_decision(
        metrics={
            "or_overlap_ratio": rr.get("or_overlap_ratio", rr.get("or_overlap_pairs_pct", 0.0)),
            "inflation_factor": rr.get("inflation_factor", None),
            "median_range_to_or": rr.get("median_range_to_or", None),
            "mean_direction_bias": rr.get("mean_direction_bias", None),
            "bias_consistency": rr.get("bias_consistency", None),
            "breakout_strength": primary.get("breakout_strength", None),
            "close_pen": primary.get("close_pen", None),
            "wick_pen": primary.get("wick_pen", None),
            "body_norm": primary.get("body_norm", None),
            "range_norm": primary.get("range_norm", None),
        },
        direction=str(primary.get("direction")),
        rules_cfg=rules_cfg,
    )

    payload = {
        "symbol": symbol,
        "asof_date": asof,
        "timestamp": primary["timestamp"],
        "direction": primary["direction"],
        "ref_high": float(primary["ref_high"]),
        "ref_low": float(primary["ref_low"]),
        "ref_width": float(primary["ref_width"]),
        "close": float(primary["close"]),
        "horizon_days": h,
        "historical_days": h,
        "also_horizons": ", ".join(str(x) for x in also) if also else "None",
        "breakout_amt": float(primary.get("breakout_amt", 0.0)),
        "breakout_strength": float(primary.get("breakout_strength", 0.0)),
        "breakout_strength_pct": int(round(float(primary.get("breakout_strength", 0.0)) * 100)),
        "close_pen": float(primary.get("close_pen", 0.0)) if primary.get("close_pen") is not None else 0.0,
        "wick_pen": float(primary.get("wick_pen", 0.0)) if primary.get("wick_pen") is not None else 0.0,
        "body_norm": float(primary.get("body_norm", 0.0)) if primary.get("body_norm") is not None else 0.0,
        "range_norm": float(primary.get("range_norm", 0.0)) if primary.get("range_norm") is not None else 0.0,
        "or_overlap_ratio": float(rr.get("or_overlap_ratio", rr.get("or_overlap_pairs_pct", 0.0)) or 0.0),
        "median_inside_own_or_pct": rr.get("median_inside_own_or_pct", None),
        "median_range_to_or": rr.get("median_range_to_or", None),
        "mean_direction_bias": rr.get("mean_direction_bias", None),
        "bias_consistency": rr.get("bias_consistency", None),
        "inflation_factor": rr.get("inflation_factor", None),
        "sessions_used": rr.get("available_days", None),
        "sessions_requested": rr.get("required_days", None),
        "is_replay": is_replay,
        **labels,
        "decision": decision_res.decision,
        "confidence": decision_res.confidence,
        "confidence_pct": int(round(decision_res.confidence * 100)),
        "decision_reasons": " | ".join(decision_res.reasons),
    }

    return render_message(templates, "default", payload)


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a session day bar-by-bar and emit multi-horizon breakout alerts.")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--date", dest="asof_date", help="YYYY-MM-DD session date to replay (defaults to run.asof_date).")
    ap.add_argument("--symbols", default="", help="Comma-separated override symbols (default uses config universe).")
    ap.add_argument("--send-discord", action="store_true", help="Send alerts to Discord webhook from config.")
    ap.add_argument("--replay", action="store_true", default=True, help="Mark alerts as replay (default true).")
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    if args.asof_date:
        cfg.setdefault("run", {})["asof_date"] = args.asof_date

    asof = str(cfg.get("run", {}).get("asof_date"))
    asof_d = date.fromisoformat(asof)

    symbols = []
    if args.symbols.strip():
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = load_symbols(cfg)

    cal = TradingCalendar(exchange=str(cfg.get("market", {}).get("exchange", "XNYS")),
                          timezone=str(cfg.get("market", {}).get("timezone", "America/Chicago")))

    if not cal.is_session(asof_d):
        raise SystemExit(f"{asof} is not a session day. Use the calendar to choose a trading day.")

    interval = str(cfg.get("data", {}).get("interval", "15m"))
    orb_minutes = int(cfg.get("orb", {}).get("orb_minutes", 30))
    horizons = list(cfg.get("reference", {}).get("horizons", []) or [])
    include_today_or = bool(cfg.get("reference", {}).get("include_today_or", False))
    min_cov = float(cfg.get("reference", {}).get("min_coverage_ratio", 0.80))

    bp_cfg = cfg.get("breakouts", {}) or {}
    params = BreakoutParams(
        close_only=bool(bp_cfg.get("close_only", True)),
        inside_reset_pct=float(bp_cfg.get("inside_reset_pct", 0.10)),
        min_bars_between_alerts=int(bp_cfg.get("min_bars_between_alerts", 0)),
        confirm_closes=int(bp_cfg.get("confirm_closes", 1)),
    )

    db = DB(_db_path(cfg))
    db.init_schema()

    dc = discord_config_from_cfg(cfg)
    send_enabled = bool(args.send_discord)

    tz = ZoneInfo(str(cfg.get("market", {}).get("timezone", "America/Chicago")))
    sess = cal.session_times(asof_d)
    # Fetch full regular session bars
    start_utc = sess.open_ts.astimezone(ZoneInfo("UTC"))
    end_utc = sess.close_ts.astimezone(ZoneInfo("UTC")) + timedelta(minutes=1)

    report_dir = _report_dir(cfg)
    import os
    os.makedirs(report_dir, exist_ok=True)

    all_events: List[Dict[str, Any]] = []

    for sym in symbols:
        # Ensure RR rows exist (DB-first OR resolution inside)
        rr_rows = compute_reference_ranges(
            db=db,
            cal=cal,
            asof_date=asof_d,
            symbol=sym,
            horizons=horizons,
            include_today_or=include_today_or,
            interval=interval,
            orb_minutes=orb_minutes,
            min_coverage_ratio=min_cov,
        )

        # Pull RR rows from DB (canonical for consistency)
        rr_rows = db.get_rr_rows(asof, sym)
        rr_by_h = {int(r["horizon_days"]): r for r in rr_rows}

        df_utc = fetch_intraday_yfinance(sym, start_utc=start_utc, end_utc=end_utc, interval=interval)
        if df_utc is None or df_utc.empty:
            continue

        df = df_utc.copy()
        df.index = df.index.tz_convert(tz)
        df = df.sort_index()
        states = init_states([int(r["horizon_days"]) for r in rr_rows])

        for i in range(len(df)):
            bar = df.iloc[i]
            # Ensure bar has expected cols
            if not all(k in bar.index for k in ("open","high","low","close")):
                continue
            evs = evaluate_bar_multi_horizon(bar=bar, rr_rows=rr_rows, params=params, states=states, bar_idx=i)
            if not evs:
                continue

            primary = choose_primary_event(evs)
            if not primary:
                continue

            # also broke horizons (same bar, same direction only)
            dirn = primary["direction"]
            broke_same_dir = sorted({int(e["horizon_days"]) for e in evs if e["direction"] == dirn})
            primary_h = int(primary["horizon_days"])
            also = [h for h in broke_same_dir if h != primary_h]

            # Persist event + per-horizon metrics
            event_id = str(uuid.uuid4())
            msg = build_message(cfg, sym, asof, primary, also, rr_by_h, is_replay=bool(args.replay))

            db.insert_breakout_event(
                {
                    "event_id": event_id,
                    "asof_date": asof,
                    "symbol": sym,
                    "timestamp": primary["timestamp"].isoformat(),
                    "direction": dirn,
                    "primary_horizon": primary_h,
                    "also_horizons_json": json.dumps(also),
                    "close": float(primary["close"]),
                    "ref_high": float(primary["ref_high"]),
                    "ref_low": float(primary["ref_low"]),
                    "ref_width": float(primary["ref_width"]),
                    "breakout_amt": float(primary["breakout_amt"]),
                    "breakout_strength": float(primary["breakout_strength"]),
                    "message": msg,
                    "is_replay": 1 if args.replay else 0,
                }
            )

            # Write per-horizon rows (all horizons)
            # Rank: horizons that broke same bar+dir are ranked by smallest horizon first.
            ranks = {h: rnk+1 for rnk, h in enumerate(broke_same_dir)}
            for rr in rr_rows:
                h = int(rr["horizon_days"])
                did_break = 1 if (h in broke_same_dir) else 0
                db.upsert_event_horizon_metrics(
                    {
                        "event_id": event_id,
                        "horizon_days": h,
        "historical_days": h,
                        "did_break": did_break,
                        "break_rank": ranks.get(h),
                        "ref_high": rr.get("ref_high"),
                        "ref_low": rr.get("ref_low"),
                        "ref_width": rr.get("ref_width"),
                        "breakout_amt": float(primary["breakout_amt"]) if did_break else None,
                        "breakout_strength": float(primary["breakout_strength"]) if did_break else None,
                        "or_overlap_pairs_pct": rr.get("or_overlap_pairs_pct"),
                        "median_inside_own_or_pct": rr.get("median_inside_own_or_pct"),
                        "median_range_to_or": rr.get("median_range_to_or"),
                        "mean_direction_bias": rr.get("mean_direction_bias"),
                        "bias_consistency": rr.get("bias_consistency"),
                    }
                )

            all_events.append({"symbol": sym, "event_id": event_id, "timestamp": primary["timestamp"].isoformat(), "direction": dirn, "primary_horizon": primary_h})

            # Discord
            if send_enabled:
                send_discord_message(dc, msg)

    # Save replay summary CSV
    if all_events:
        pd.DataFrame(all_events).to_csv(f"{report_dir}/replay_events_{asof}.csv", index=False)

    db.close()
    print(f"Replay complete for {asof}. Events: {len(all_events)}. Output dir: {report_dir}")


if __name__ == "__main__":
    main()
