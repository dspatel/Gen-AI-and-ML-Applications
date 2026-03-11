import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import yaml
from datetime import date

import pandas as pd

from orb_ref.universe import load_symbols
from orb_ref.sessions import TradingSessions
from orb_ref.data_fetch import FetchSpec, fetch_lookback_bundle
from orb_ref.ranges_or import compute_daily_or
from orb_ref.lookback_behavior import compute_day_behavior
from orb_ref.horizons import build_horizon_results
from orb_ref.reporting_daily import write_daily_report
from orb_ref.storage.store_factory import make_store


def main():
    cfg = yaml.safe_load(open("config/config.example.yml", encoding="utf-8"))
    symbols = load_symbols(cfg)

    asof = date.fromisoformat(cfg["run"]["asof_date"])
    tz = cfg.get("market", {}).get("timezone", "America/Chicago")
    exchange = cfg.get("market", {}).get("exchange", "XNYS")
    interval = cfg.get("data", {}).get("interval", "15m")
    orb_minutes = int(cfg.get("orb", {}).get("orb_minutes", 30))

    horizons = cfg.get("reference", {}).get("horizons") or [int(cfg.get("reference", {}).get("historical_days", 5))]
    horizons = [int(x) for x in horizons]
    max_h = max(horizons) if horizons else int(cfg.get("reference", {}).get("historical_days", 5))
    include_today_or = bool(cfg.get("reference", {}).get("include_today_or", False))
    min_sessions_required = int(cfg.get("reference", {}).get("min_sessions_required", 3))

    ts = TradingSessions(exchange=exchange, tz=tz)

    rows = []
    or_rows_to_store = []
    for sym in symbols:
        spec = FetchSpec(symbol=sym, asof_date=asof, interval=interval, tz=tz, exchange=exchange)

        sessions, frames = fetch_lookback_bundle(
            spec,
            historical_days=max_h,
            include_today_or=include_today_or,
        )

        sessions_requested = len(sessions)
        empty_days = [d for d, df in frames.items() if df is None or df.empty]
        sessions_nonempty = sessions_requested - len(empty_days)

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

            # attach keys for storage
            or_row_store = {
                "session_date": sess_date.isoformat(),
                "symbol": sym,
                "interval": interval,
                "orb_minutes": orb_minutes,
                "or_start": str(or_start),
                "or_end": str(or_end),
                "or_high": float(or_row["or_high"]),
                "or_low": float(or_row["or_low"]),
                "or_width": float(or_row["or_width"]),
                "source": "computed",
            }
            or_rows_to_store.append(or_row_store)

            per_day_or.append(or_row)
            per_day_behavior.append(compute_day_behavior(df, or_row["or_high"], or_row["or_low"]))

        horizon_results = build_horizon_results(
            per_day_or,
            per_day_behavior,
            horizons=horizons,
            min_sessions_required=min_sessions_required,
        )

        for h, hr in horizon_results.items():
            if not hr.ref:
                continue
            row = {
                "asof_date": asof.isoformat(),
                "symbol": sym,
                "horizon_days": int(h),
                "sessions_requested": hr.sessions_requested,
                "sessions_nonempty": sessions_nonempty,
                "sessions_used": hr.sessions_used,
                "sessions_missing_data": ",".join(hr.sessions_missing_data),

                "ref_high": hr.ref.get("ref_high"),
                "ref_low": hr.ref.get("ref_low"),
                "ref_width": hr.ref.get("ref_width"),
                "inflation_factor": hr.ref.get("inflation_factor"),

                "or_overlap_pairs_pct": hr.overlap.get("or_overlap_pairs_pct"),
                "or_overlap_days_count": hr.overlap.get("or_overlap_days_count"),
                "or_days": hr.overlap.get("or_days"),

                **hr.behavior,
                "horizon_active": 1 if hr.active else 0,
                "horizon_inactive_reason": hr.inactive_reason or "",
            }
            rows.append(row)

    # Write combined daily metrics for all symbols+horizons
    out = write_daily_report(rows, asof, out_dir="reports/daily")
    print("Wrote:", out)

    # Upsert to DB
    if cfg.get("storage", {}).get("enabled", False):
        store = make_store(cfg)
        if or_rows_to_store:
            store.upsert_daily_or(pd.DataFrame(or_rows_to_store))
        n = store.upsert_daily_metrics_v2(pd.DataFrame(rows), run_context={
            "interval": interval,
            "orb_minutes": orb_minutes,
            "include_today_or": include_today_or,
        })
        print(f"DB upsert daily metrics v2: {n} rows -> {store.db_path}")

if __name__ == "__main__":
    main()
