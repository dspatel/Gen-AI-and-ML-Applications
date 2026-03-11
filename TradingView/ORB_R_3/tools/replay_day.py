import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import argparse
import os
from datetime import date
from pathlib import Path
import yaml
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
from orb_ref.notifications import load_templates, render_message
from orb_ref.decision import load_decision_rules, decide
from orb_ref.story import load_story_config, build_market_story
from orb_ref.notifier import build_notifier
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Replay date YYYY-MM-DD")
    ap.add_argument("--symbol", required=False, help="Symbol (if omitted, use universe list)")
    ap.add_argument("--print-events", action="store_true")
    ap.add_argument("--delay", type=float, default=0.0, help="Seconds between bars (live-like)")
    ap.add_argument("--send", action="store_true", help="Send Discord notifications if enabled")
    args = ap.parse_args()

    # Prefer user config, fall back to template
    cfg_path = "config/config.yml" if os.path.exists("config/config.yml") else "config/config.example.yml"
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    asof = date.fromisoformat(args.date)

    symbols = [args.symbol] if args.symbol else load_symbols(cfg)

    tz = cfg.get("market", {}).get("timezone", "America/Chicago")
    tz_abbrev = 'CT' if tz == 'America/Chicago' else ('ET' if tz == 'America/New_York' else '')
    exchange = cfg.get("market", {}).get("exchange", "XNYS")
    interval = cfg.get("data", {}).get("interval", "15m")
    orb_minutes = int(cfg.get("orb", {}).get("orb_minutes", 30))

    horizons = cfg.get("reference", {}).get("horizons", [cfg.get("reference", {}).get("historical_days", 5)])
    horizons = [int(x) for x in horizons]

    include_today_or = bool(cfg.get("reference", {}).get("include_today_or", False))
    min_sessions_required = int(cfg.get("reference", {}).get("min_sessions_required", 3))

    bo_cfg = cfg.get("breakouts", {}) or {}
    params = BreakoutParams(
        close_required=True,
        inside_reset_pct=float(bo_cfg.get("inside_reset_pct", 0.10)),
        confirm_closes=int(bo_cfg.get("confirm_closes", 1)),
        min_bars_between_alerts=int(bo_cfg.get("min_bars_between_alerts", 0)),
    )

    ts = TradingSessions(exchange=exchange, tz=tz)
    root = Path(__file__).resolve().parents[1]
    label_engine = LabelEngine(str(root / "config" / "labels.yml"))
    templates = load_templates(str(root / "config" / "notification_templates.yml"))
    decision_rules = load_decision_rules(str(root / "config" / "decision_rules.yml"))
    story_cfg = load_story_config(str(root / "config" / "story.yml"))

    notifier = build_notifier(cfg)

    # Optional: persist daily OR rows + breakout events in SQLite.
    # Many users expect replay runs to populate the DB; enabling storage does that.
    store = None
    storage_cfg = cfg.get("storage", {})
    if storage_cfg.get("enabled", False):
        store = make_store(cfg)

    # Accumulate rows so a replay can optionally populate the DB.
    all_or_rows = []
    all_events = []

    for sym in symbols:
        spec = FetchSpec(symbol=sym, asof_date=asof, interval=interval, tz=tz, exchange=exchange)

        # Fetch enough lookback for max horizon
        max_h = max(horizons) if horizons else 5
        sessions, frames = fetch_lookback_bundle(spec, historical_days=max_h, include_today_or=include_today_or)

        # Ensure intraday bars for asof exist
        today_df = frames.get(asof)
        if today_df is None or today_df.empty:
            today_df = fetch_session_bars(spec, asof)
        if today_df is None or today_df.empty:
            print("Missing intraday data for replay date.")
            continue

        # compute OR rows for lookback sessions (exclude asof OR by default unless include_today_or and OR window exists)
        or_rows = []
        beh_rows = []
        for sess_date, df in frames.items():
            if df is None or df.empty:
                continue
            if sess_date == asof and not include_today_or:
                continue
            or_start, or_end = ts.get_or_window_bounds(sess_date, orb_minutes)
            row = compute_daily_or(df, or_start, or_end)
            if not row:
                continue
            # Enrich the OR row with keys required for DB persistence.
            row = {
                **row,
                "symbol": sym,
                "session_date": sess_date.isoformat(),
                "interval": interval,
                "orb_minutes": int(orb_minutes),
            }
            or_rows.append(row)
            beh_rows.append(compute_day_behavior(df, row["or_high"], row["or_low"]))

        # Keep all OR rows so we can optionally persist them to SQLite once per run.
        all_or_rows.extend(or_rows)

        # Build horizon refs
        horizon_res = build_horizon_results(or_rows, beh_rows, horizons=horizons, min_sessions_required=min_sessions_required)

        refs_by_h = {h: hr.ref for h, hr in horizon_res.items() if hr.active}

        if not refs_by_h:
            print("Missing reference range for replay date.")
            continue

        # Step through bars and run ladder
        state = LadderState.fresh()
        events = []
        for ts_idx, bar in today_df.iterrows():
            chunk = today_df.loc[[ts_idx]]
            new_events, state = run_ladder_stepwise(chunk, refs_by_h, params=params, state=state, order="ASC")
            for e in new_events:
                h = e["horizon_days"]
                ref = refs_by_h[h]
                intensity = compute_breakout_intensity(bar, ref_low=ref["ref_low"], ref_high=ref["ref_high"], ref_width=ref["ref_width"], direction=e["direction"])
                # Build labels/decision/story per horizon
                # Combine metrics for labels
                metrics = {**ref, **horizon_res[h].behavior, **horizon_res[h].overlap, **intensity}
                labels = label_engine.build_labels(metrics, use_icons=True)
                decision_res = decide(metrics, e["direction"], decision_rules)
                story = build_market_story(metrics, decision_res.decision, decision_res.confidence, labels, story_cfg).story

                payload = {
                    "symbol": sym,
                    "asof_date": asof.isoformat(),
                    "timestamp": str(e["timestamp"]),
                    "event_time": _fmt_event_time(e["timestamp"], tz_label=tz_abbrev),
                    "direction": e["direction"],
                    "horizon_days": h,
                    "ref_high": ref["ref_high"],
                    "ref_low": ref["ref_low"],
                    "ref_width": ref["ref_width"],
                    "inflation_factor": ref.get("inflation_factor"),
                    "or_overlap_pairs_pct": horizon_res[h].overlap.get("or_overlap_pairs_pct"),
                    "or_overlap_days_count": horizon_res[h].overlap.get("or_overlap_days_count"),
                    "or_days": horizon_res[h].overlap.get("or_days"),
                    "or_overlap_adjacent_count": horizon_res[h].overlap.get("or_overlap_adjacent_count"),
                    "or_overlap_adjacent_total": horizon_res[h].overlap.get("or_overlap_adjacent_total"),
                    "or_overlap_adjacent_pct": horizon_res[h].overlap.get("or_overlap_adjacent_pct"),
                    "confirm_closes": params.confirm_closes,
                    **intensity,
                    **{f"label_{k}": v for k, v in labels.items()},
                    "decision": decision_res.decision,
                    "confidence": decision_res.confidence,
                    "confidence_pct": int(round(decision_res.confidence * 100)),
                    "decision_reasons": " • ".join(decision_res.reasons),
                    "story": story,
                    "broken_horizons_before": e.get("broken_horizons_before",""),
                    "broken_horizons_after": e.get("broken_horizons_after",""),
                    "simultaneous_horizons": e.get("simultaneous_horizons",""),
                    "title": f"{sym} {e['direction']} Breakout ({h}D)",
                }

                msg = render_message(templates, "default", payload)

                if args.print_events:
                    print(payload["title"])
                    print(msg)
                    print("-"*80)

                if args.send and bool(cfg.get("notifications", {}).get("enabled", False)):
                    mode = (cfg.get("run", {}).get("mode", "TEST") or "TEST").upper()
                    if (mode != "TEST") or bool(cfg.get("notifications", {}).get("send_in_test_mode", True)):
                        status, _ = notifier.send(payload["title"], msg)
                        if status and status >= 400:
                            print(f"[discord] send failed with status {status}")

                events.append({**payload, "message": msg, **intensity})

            if args.delay and args.delay > 0:
                import time
                time.sleep(args.delay)

        # write replay events csv
        out_dir = Path("reports/replay")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{asof.isoformat()}_{sym}_replay_events.csv"
        pd.DataFrame(events).to_csv(out_path, index=False)
        print("Wrote:", out_path)

        # Accumulate so we can optionally persist once per run.
        if events:
            all_events.extend(events)

    # Optional persistence: write all rows after processing all symbols.
    if store is not None:
        if all_or_rows:
            store.upsert_daily_or(pd.DataFrame(all_or_rows))
        if all_events:
            store.upsert_breakout_events_v2(pd.DataFrame(all_events))
        print(f"[storage] upserted daily_or={len(all_or_rows)} breakout_events={len(all_events)}")


if __name__ == "__main__":
    main()
