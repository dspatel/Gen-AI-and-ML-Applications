import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import yaml
from datetime import date
from pathlib import Path
import pandas as pd
from dateutil import parser as dtparser

from orb_ref.universe import load_symbols
from orb_ref.sessions import TradingSessions
from orb_ref.data_fetch import FetchSpec, fetch_lookback_bundle, fetch_session_bars
from orb_ref.ranges_or import compute_daily_or
from orb_ref.lookback_behavior import compute_day_behavior
from orb_ref.horizons import build_horizon_results
from orb_ref.breakouts import BreakoutParams
from orb_ref.ladder import LadderState, run_ladder_stepwise
from orb_ref.intensity import compute_breakout_intensity
from orb_ref.interpretation.label_engine import LabelEngine
from orb_ref.decision import load_decision_rules, decide
from orb_ref.notifications import load_templates, render_message
from orb_ref.story import load_story_config, build_market_story
from orb_ref.storage.store_factory import make_store



def _fmt_event_time(ts: str, tz_label: str = "") -> str:
    """Format an ISO timestamp into a compact local-time string for alerts."""
    try:
        dt = dtparser.isoparse(str(ts))
        # If timestamp has tzinfo, keep it; otherwise treat as naive.
        if getattr(dt, 'tzinfo', None) is None:
            return dt.strftime('%Y-%m-%d %H:%M')
        # Abbrev is platform-dependent; we keep offset + optional label.
        base = dt.strftime('%Y-%m-%d %H:%M')
        off = dt.strftime('%z')
        off = (off[:3] + ':' + off[3:]) if off and len(off) == 5 else off
        if tz_label:
            return f"{base} {tz_label} ({off})"
        return f"{base} ({off})"
    except Exception:
        return str(ts)

def main():
    cfg = yaml.safe_load(open("config/config.example.yml", encoding="utf-8"))
    symbols = load_symbols(cfg)

    asof = date.fromisoformat(cfg["run"]["asof_date"])
    tz = cfg.get("market", {}).get("timezone", "America/Chicago")
    tz_abbrev = 'CT' if tz == 'America/Chicago' else ('ET' if tz == 'America/New_York' else '')
    exchange = cfg.get("market", {}).get("exchange", "XNYS")
    interval = cfg.get("data", {}).get("interval", "15m")
    orb_minutes = int(cfg.get("orb", {}).get("orb_minutes", 30))

    horizons = cfg.get("reference", {}).get("horizons") or [int(cfg.get("reference", {}).get("historical_days", 5))]
    horizons = [int(x) for x in horizons]
    max_h = max(horizons) if horizons else 5
    include_today_or = bool(cfg.get("reference", {}).get("include_today_or", False))
    min_sessions_required = int(cfg.get("reference", {}).get("min_sessions_required", 3))

    bcfg = cfg.get("breakouts", {}) or {}
    params = BreakoutParams(
        close_required=bool(bcfg.get("close_required", True)),
        confirm_closes=int(bcfg.get("confirm_closes", 1)),
        inside_reset_pct=float(bcfg.get("inside_reset_pct", 0.10)),
        min_bars_between_alerts=int(bcfg.get("min_bars_between_alerts", 0)),
    )

    ts = TradingSessions(exchange=exchange, tz=tz)
    root = Path(__file__).resolve().parents[1]
    label_engine = LabelEngine(str(root / "config" / "labels.yml"))
    templates = load_templates(str(root / "config" / "notification_templates.yml"))
    decision_rules = load_decision_rules(str(root / "config" / "decision_rules.yml"))
    story_cfg = load_story_config(str(root / "config" / "story.yml"))

    all_events = []

    for sym in symbols:
        spec = FetchSpec(symbol=sym, asof_date=asof, interval=interval, tz=tz, exchange=exchange)

        sessions, frames = fetch_lookback_bundle(
            spec,
            historical_days=max_h,
            include_today_or=include_today_or,
        )

        # Ensure intraday bars for asof
        today_df = frames.get(asof)
        if today_df is None or today_df.empty:
            today_df = fetch_session_bars(spec, asof)

        if today_df is None or today_df.empty:
            continue

        # compute OR + behavior for lookback sessions
        per_day_or = []
        per_day_behavior = []
        for sess_date, df in frames.items():
            if df is None or df.empty:
                continue
            if sess_date == asof and not include_today_or:
                continue
            or_start, or_end = ts.get_or_window_bounds(sess_date, orb_minutes)
            or_row = compute_daily_or(df, or_start, or_end)
            if not or_row:
                continue
            per_day_or.append(or_row)
            per_day_behavior.append(compute_day_behavior(df, or_row["or_high"], or_row["or_low"]))

        horizon_results = build_horizon_results(
            per_day_or,
            per_day_behavior,
            horizons=horizons,
            min_sessions_required=min_sessions_required,
        )

        refs_by_h = {h: hr.ref for h, hr in horizon_results.items() if hr.active}
        if not refs_by_h:
            continue

        state = LadderState.fresh()
        sym_events = []
        for ts_idx, bar in today_df.iterrows():
            chunk = today_df.loc[[ts_idx]]
            new_events, state = run_ladder_stepwise(chunk, refs_by_h, params=params, state=state, order="ASC")
            for e in new_events:
                h = int(e["horizon_days"])
                ref = refs_by_h[h]
                intensity = compute_breakout_intensity(bar, ref_low=ref["ref_low"], ref_high=ref["ref_high"], ref_width=ref["ref_width"], direction=e["direction"])
                metrics = {**ref, **horizon_results[h].behavior, **horizon_results[h].overlap, **intensity}
                labels = label_engine.build_labels(metrics, use_icons=True)
                decision_res = decide(metrics, e["direction"], decision_rules)
                story = build_market_story(metrics, decision_res.decision, decision_res.confidence, labels, story_cfg).story

                payload = {
                    "asof_date": asof.isoformat(),
                    "symbol": sym,
                    "timestamp": str(e["timestamp"]),
                    "event_time": _fmt_event_time(e["timestamp"], tz_label=tz_abbrev),
                    "direction": e["direction"],
                    "horizon_days": h,
                    **intensity,
                    "ref_high": ref["ref_high"],
                    "ref_low": ref["ref_low"],
                    "ref_width": ref["ref_width"],
                    **intensity,
                    **{f"label_{k}": v for k, v in labels.items()},
                    "decision": decision_res.decision,
                    "confidence": decision_res.confidence,
                    "confidence_pct": int(round(decision_res.confidence * 100)),
                    "decision_reasons": " • ".join(decision_res.reasons),
                    "story": story,
                    "or_days": int(horizon_results[h].overlap.get("or_days") or 0),
                    "or_overlap_days_count": int(horizon_results[h].overlap.get("or_overlap_days_count") or 0),
                    "or_overlap_pairs_pct": float(horizon_results[h].overlap.get("or_overlap_pairs_pct") or 0.0),
                    "or_overlap_adjacent_count": int(horizon_results[h].overlap.get("or_overlap_adjacent_count") or 0),
                    "or_overlap_adjacent_total": int(horizon_results[h].overlap.get("or_overlap_adjacent_total") or 0),
                    "or_overlap_adjacent_pct": float(horizon_results[h].overlap.get("or_overlap_adjacent_pct") or 0.0),
                    "inflation_factor": ref.get("inflation_factor"),
                    "median_inside_own_or_pct": horizon_results[h].behavior.get("median_inside_own_or_pct"),
                    "median_range_to_or": horizon_results[h].behavior.get("median_range_to_or"),
                    "mean_direction_bias": horizon_results[h].behavior.get("mean_direction_bias"),
                    "bias_consistency": horizon_results[h].behavior.get("bias_consistency"),
                    "sessions_used": horizon_results[h].sessions_used,
                    "simultaneous_horizons": e.get("simultaneous_horizons", ""),
                    "broken_horizons_before": e.get("broken_horizons_before", ""),
                    "broken_horizons_after": e.get("broken_horizons_after", ""),
                }

                msg = render_message(templates, "default", {
                    **payload,
                    "title": f"{sym} {e['direction']} Breakout ({h}D)"
                })
                payload["message"] = msg
                sym_events.append(payload)

        if sym_events:
            out_dir = Path("reports/daily")
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{asof.isoformat()}_{sym}_events.csv"
            pd.DataFrame(sym_events).to_csv(out_path, index=False)
            print("Wrote:", out_path)
            all_events.extend(sym_events)

    if cfg.get("storage", {}).get("enabled", False) and all_events:
        store = make_store(cfg)
        n = store.upsert_breakout_events_v2(pd.DataFrame(all_events))
        print(f"DB upsert breakout events v2: {n} rows -> {store.db_path}")


if __name__ == "__main__":
    main()
