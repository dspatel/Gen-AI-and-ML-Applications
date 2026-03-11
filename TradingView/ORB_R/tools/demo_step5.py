import yaml
from datetime import date

import pandas as pd

from orb_ref.universe import load_symbols
from orb_ref.sessions import TradingSessions
from orb_ref.data_fetch import FetchSpec, fetch_lookback_bundle
from orb_ref.ranges_or import compute_daily_or
from orb_ref.reference_range import build_reference_range
from orb_ref.lookback_behavior import compute_day_behavior, aggregate_behavior
from orb_ref.reporting_daily import write_daily_report
from orb_ref.storage.store_factory import make_store


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

    ts = TradingSessions(exchange=exchange, tz=tz)

    rows = []
    for sym in symbols:
        spec = FetchSpec(symbol=sym, asof_date=asof, interval=interval, tz=tz, exchange=exchange)

        sessions, frames = fetch_lookback_bundle(
            spec,
            historical_days=historical_days,
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
            or_start, or_end = ts.get_or_window_bounds(sess_date, orb_minutes)
            or_row = compute_daily_or(df, or_start, or_end)
            if not or_row:
                continue
            per_day_or.append(or_row)
            per_day_behavior.append(compute_day_behavior(df, or_row["or_high"], or_row["or_low"]))

        ref = build_reference_range(per_day_or)
        beh = aggregate_behavior(per_day_behavior)

        if not ref:
            # still record session diagnostics so you can see why nothing was computed
            rows.append({
                "asof_date": asof,
                "symbol": sym,
                "sessions_requested": sessions_requested,
                "sessions_nonempty": sessions_nonempty,
                "sessions_used": len(per_day_or),
                "sessions_missing_data": ",".join([d.isoformat() for d in empty_days]),
            })
            continue

        rows.append({
            "asof_date": asof,
            "symbol": sym,
            "sessions_requested": sessions_requested,
            "sessions_nonempty": sessions_nonempty,
            "sessions_used": len(per_day_or),
            "sessions_missing_data": ",".join([d.isoformat() for d in empty_days]),
            **ref,
            **beh,
        })

    out = write_daily_report(rows, asof)
    print("Wrote:", out)

    # Optional DB upsert
    storage_cfg = cfg.get("storage", {}) or {}
    if bool(storage_cfg.get("enabled", False)):
        store = make_store(cfg)
        df = pd.read_csv(out)
        run_ctx = {
            "interval": interval,
            "orb_minutes": orb_minutes,
            "historical_days": historical_days,
            "include_today_or": include_today_or,
        }
        n = store.upsert_daily_metrics(df, run_context=run_ctx)
        print(f"DB upsert daily metrics: {n} rows -> {store.db_path}")


if __name__ == "__main__":
    main()
