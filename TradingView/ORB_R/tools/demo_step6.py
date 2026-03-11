import yaml
from datetime import date
from pathlib import Path

from orb_ref.universe import load_symbols
from orb_ref.sessions import TradingSessions
from orb_ref.data_fetch import FetchSpec, fetch_lookback_bundle, fetch_session_bars
from orb_ref.ranges_or import compute_daily_or
from orb_ref.reference_range import build_reference_range
from orb_ref.lookback_behavior import compute_day_behavior, aggregate_behavior
from orb_ref.breakouts import BreakoutParams, detect_breakouts_close_only
from orb_ref.intensity import compute_breakout_intensity
from orb_ref.interpretation.label_engine import LabelEngine
from orb_ref.notifications import load_templates, render_message
from orb_ref.decision import load_decision_rules, decide
from orb_ref.storage.store_factory import make_store

import pandas as pd


def write_events(events, asof_date, out_dir="reports/daily"):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(events)
    if df.empty:
        # still write an empty file with headers for consistency
        df = pd.DataFrame(columns=["asof_date","symbol","timestamp","direction","message"])
    df.insert(0, "asof_date", asof_date)
    path = Path(out_dir) / f"{asof_date}_events.csv"
    df.to_csv(path, index=False)
    return path


def main():
    cfg = yaml.safe_load(open("config/config.example.yml", encoding="utf-8"))
    symbols = load_symbols(cfg)

    asof = date.fromisoformat(cfg["run"]["asof_date"])
    tz = cfg.get("market", {}).get("timezone", "America/Chicago")
    exchange = cfg.get("market", {}).get("exchange", "XNYS")
    interval = cfg.get("data", {}).get("interval", "1m")
    orb_minutes = int(cfg.get("orb", {}).get("orb_minutes", 30))
    historical_days = int(cfg.get("reference", {}).get("historical_days", 5))
    include_today_or = bool(cfg.get("reference", {}).get("include_today_or", False))

    bo_cfg = cfg.get("breakouts", {}) or {}
    close_required = bool(bo_cfg.get("confirm", {}).get("close_required", True))
    inside_reset_pct = float(bo_cfg.get("rearm", {}).get("inside_reset_pct", 0.10))

    ts = TradingSessions(exchange=exchange, tz=tz)

    root = Path(__file__).resolve().parents[1]
    label_engine = LabelEngine(str(root / "config" / "labels.yml"))
    templates = load_templates(str(root / "config" / "notification_templates.yml"))
    decision_rules = load_decision_rules(str(root / "config" / "decision_rules.yml"))

    all_events = []

    for sym in symbols:
        spec = FetchSpec(symbol=sym, asof_date=asof, interval=interval, tz=tz, exchange=exchange)

        # Lookback bundle (for reference range + behavior)
        sessions, frames = fetch_lookback_bundle(
            spec,
            historical_days=historical_days,
            include_today_or=include_today_or,
        )

        per_day_or = []
        per_day_behavior = []
        for sess_date, df in frames.items():
            if df is None or df.empty:
                continue
            or_start, or_end = ts.get_or_window_bounds(sess_date, orb_minutes)
            or_row = compute_daily_or(df, or_start, or_end)
            if not or_row:
                continue
            per_day_or.append(or_row)
            per_day_behavior.append(compute_day_behavior(df, or_row["or_high"], or_row["or_low"]))

        ref = build_reference_range(per_day_or)
        beh = aggregate_behavior(per_day_behavior)

        if not ref:
            continue

        # Fetch today's session bars for event detection (anchor session)
        # Note: if asof is not a trading day, fetch_session_bars will raise; keep this strict for now.
        today_df = fetch_session_bars(spec, asof)

        params = BreakoutParams(close_required=close_required, inside_reset_pct=inside_reset_pct)
        events = detect_breakouts_close_only(today_df, ref["ref_low"], ref["ref_high"], params=params)

        for e in events:
            bar = today_df.loc[e["timestamp"]]
            intensity = compute_breakout_intensity(
                bar,
                ref_low=ref["ref_low"],
                ref_high=ref["ref_high"],
                ref_width=ref["ref_width"],
                direction=e["direction"],
            )

            # Build payload for labels + message
            metrics = {
                **ref,
                **beh,
                **intensity,
            }
            labels = label_engine.build_labels(metrics, use_icons=True)

            decision_res = decide(metrics, e["direction"], decision_rules)

            payload = {
                "symbol": sym,
                "direction": e["direction"],
                "ref_low": ref["ref_low"],
                "ref_high": ref["ref_high"],
                "ref_width": ref["ref_width"],
                "historical_days": historical_days,
                "include_today_or": include_today_or,
                "sessions_used": len(per_day_or),
                "open_alignment": labels["open_alignment"],
                "reference_shape": labels["reference_shape"],
                "regime": labels["regime"],
                "direction_bias": labels["direction_bias"],
                "breakout_strength": labels["breakout_strength"],
                "decision": decision_res.decision,
                "confidence": decision_res.confidence,
                "confidence_pct": int(round(decision_res.confidence*100)),
                "decision_reasons": " • ".join(decision_res.reasons),
                "close_pen": intensity["close_pen"],
                "wick_pen": intensity["wick_pen"],
                "body_norm": intensity["body_norm"],
            }
            msg = render_message(templates, "default", payload)

            all_events.append({
                "symbol": sym,
                "timestamp": e["timestamp"],
                "direction": e["direction"],
                **ref,
                **beh,
                **intensity,
                **{f"label_{k}": v for k, v in labels.items()},
                "decision": decision_res.decision,
                "confidence": decision_res.confidence,
                "decision_reasons": " | ".join(decision_res.reasons),
                "message": msg,
            })

    out = write_events(all_events, asof)
    print("Wrote:", out)

    storage_cfg = cfg.get("storage", {}) or {}
    if bool(storage_cfg.get("enabled", False)):
        store = make_store(cfg)
        import pandas as pd
        df = pd.read_csv(out)
        n = store.upsert_breakout_events(df)
        print(f"DB upsert breakout events: {n} rows -> {store.db_path}")
        if all_events:
            print("\nSample message:\n")
            print(all_events[0]["message"])


if __name__ == "__main__":
    main()
