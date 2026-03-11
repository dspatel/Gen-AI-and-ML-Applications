from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

# Ensure repo root is on sys.path (no env vars)
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config_loader import load_yaml, load_symbols
from src.db import DB
from src.calendar import TradingCalendar
from src.or_compute import resolve_daily_or
from src.rr_compute import compute_reference_ranges
from src.audit import write_csv

def main() -> None:
    cfg = load_yaml(REPO_ROOT / "config" / "config.yaml")
    mode = (cfg.get("run", {}).get("mode") or "TEST").upper()

    tz = cfg["market"]["timezone"]
    exchange = cfg["market"]["exchange"]

    interval = cfg["data"]["interval"]
    orb_minutes = int(cfg["orb"]["orb_minutes"])

    horizons = cfg["reference"]["horizons"]
    include_today_or = bool(cfg["reference"].get("include_today_or", False))
    min_cov = float(cfg["reference"].get("min_coverage_ratio", 0.80))

    asof = date.fromisoformat(cfg["run"]["asof_date"])

    out_dir = Path(cfg["reporting"]["output_dir_test" if mode == "TEST" else "output_dir_prod"])
    db_path = cfg["storage"]["db_path_test" if mode == "TEST" else "db_path_prod"]

    symbols = load_symbols(cfg)

    db = DB(str(REPO_ROOT / db_path))
    db.init_schema()

    cal = TradingCalendar(exchange=exchange, timezone=tz)

    # OR audit: resolve OR for the last max(horizons) prior sessions (to warm cache)
    warm_days = max(int(h) for h in horizons)
    prior_days = cal.previous_sessions(asof=asof, n=warm_days)

    or_audit_rows = []
    for sym in symbols:
        for d in prior_days:
            row, reason = resolve_daily_or(db, cal, sym, d, interval=interval, orb_minutes=orb_minutes)
            or_audit_rows.append({
                "session_date": d.isoformat(),
                "symbol": sym,
                "status": reason,
                "or_high": None if not row else row.get("or_high"),
                "or_low": None if not row else row.get("or_low"),
                "or_width": None if not row else row.get("or_width"),
            })

    write_csv(
        str(REPO_ROOT / out_dir / "or_audit.csv"),
        or_audit_rows,
        fieldnames=["session_date","symbol","status","or_high","or_low","or_width"],
    )

    # RR audit: compute per horizon independently with coverage eval
    rr_audit_rows = []
    for sym in symbols:
        rr_rows = compute_reference_ranges(
            db=db,
            cal=cal,
            asof_date=asof,
            symbol=sym,
            horizons=horizons,
            include_today_or=include_today_or,
            interval=interval,
            orb_minutes=orb_minutes,
            min_coverage_ratio=min_cov,
        )
        for r in rr_rows:
            rr_audit_rows.append({
                "asof_date": r["asof_date"],
                "symbol": r["symbol"],
                "horizon_days": r["horizon_days"],
                "is_valid": r["is_valid"],
                "coverage_ratio": r["coverage_ratio"],
                "required_days": r["required_days"],
                "available_days": r["available_days"],
                "missing_or_dates_json": r["missing_or_dates_json"],
                "failure_reason": r["failure_reason"],
                "ref_high": r["ref_high"],
                "ref_low": r["ref_low"],
                "ref_width": r["ref_width"],

                "pairs_total": r.get("pairs_total"),
                "or_overlap_pairs_count": r.get("or_overlap_pairs_count"),
                "or_overlap_pairs_pct": r.get("or_overlap_pairs_pct"),
                "or_overlap_days_count": r.get("or_overlap_days_count"),
                "or_overlap_days_pct": r.get("or_overlap_days_pct"),

                "median_inside_own_or_pct": r.get("median_inside_own_or_pct"),
                "median_range_to_or": r.get("median_range_to_or"),
                "mean_direction_bias": r.get("mean_direction_bias"),
                "bias_consistency": r.get("bias_consistency"),
                "behavior_days_required": r.get("behavior_days_required"),
                "behavior_days_available": r.get("behavior_days_available"),
                "behavior_days_missing_json": r.get("behavior_days_missing_json"),
                "behavior_failure_reason": r.get("behavior_failure_reason"),
                "used_today_or": r["used_today_or"],
                "today_or_ready": r["today_or_ready"],
            })

    write_csv(
        str(REPO_ROOT / out_dir / "rr_audit.csv"),
        rr_audit_rows,
        fieldnames=[
            "asof_date","symbol","horizon_days","is_valid","coverage_ratio",
            "required_days","available_days","missing_or_dates_json","failure_reason",
            "ref_high","ref_low","ref_width","used_today_or","today_or_ready"
            ,"pairs_total","or_overlap_pairs_count","or_overlap_pairs_pct","or_overlap_days_count","or_overlap_days_pct"
            ,"median_inside_own_or_pct","median_range_to_or","mean_direction_bias","bias_consistency"
            ,"behavior_days_required","behavior_days_available","behavior_days_missing_json","behavior_failure_reason"
        ],
    )

    db.close()
    print(f"Done. Wrote audits to: {out_dir}/or_audit.csv and {out_dir}/rr_audit.csv")
    print(f"DB: {db_path}")

if __name__ == "__main__":
    main()
