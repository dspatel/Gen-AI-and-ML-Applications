from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

import exit_optimizer_walkforward as ex

DEFAULT_SYMBOLS_FILE = Path(__file__).resolve().parent / "universes" / "focus_symbols_v1.txt"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "alpaca_daily_cache.sqlite"
DEFAULT_REPORT = Path(__file__).resolve().parent / "reports" / "short_symbol_holdout_report.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Per-symbol short report using holdout split. "
            "Compares baseline fixed exit vs locked short preset vs optimized short-focused exit."
        )
    )
    p.add_argument("--symbols", default="")
    p.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS_FILE))
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex")
    p.add_argument("--adjustment", choices=["raw", "split", "dividend", "all"], default="split")
    p.add_argument("--breakout-window", type=int, default=21)
    p.add_argument("--range-mode", choices=["rolling", "anchored"], default="anchored")
    p.add_argument("--setup-max-days", type=int, default=15)
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--baseline-fixed-days", type=int, default=10)
    p.add_argument("--max-hold-cap", type=int, default=40)
    p.add_argument(
        "--candidate-profile",
        choices=["short_focus", "short_conservative"],
        default="short_focus",
        help="Optimization grid profile for per-symbol tuning on train split.",
    )
    p.add_argument("--save-report-csv", default=str(DEFAULT_REPORT))
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    symbols = ex._parse_symbols(args)
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Day"

    conn = sqlite3.connect(str(Path(args.db_path)))
    daily = ex._read_daily(conn, source_key=source_key, symbols=symbols)
    conn.close()
    if daily.empty:
        raise RuntimeError("No daily data found in DB for requested symbols/source.")

    baseline_cfg = {"exit_type": "fixed", "fixed_days": int(args.baseline_fixed_days)}
    locked_cfg = {
        "exit_type": "hybrid",
        "atr_mult": 1.5,
        "hard_stop_atr": 1.0,
        "breakeven_r": 0.0,
        "use_ema_flip": False,
        "max_hold": 15,
    }
    short_focus_candidates = ex._candidate_grid(
        max_hold_cap=int(args.max_hold_cap),
        profile=str(args.candidate_profile),
    )

    rows: list[dict] = []
    for sym in sorted(daily["symbol"].unique()):
        ds = daily[daily["symbol"] == sym].copy()
        entries = ex._build_entries(
            daily=ds,
            breakout_window=int(args.breakout_window),
            range_mode=str(args.range_mode),
            setup_max_days=int(args.setup_max_days),
            side_mode="short",
        )
        if entries.empty:
            continue

        dts = sorted(pd.to_datetime(ds["date"]).dt.normalize().unique().tolist())
        if len(dts) < 80:
            continue
        split_idx = int(len(dts) * float(args.train_ratio))
        split_idx = max(40, min(split_idx, len(dts) - 20))
        train_dates = set(dts[:split_idx])
        test_dates = set(dts[split_idx:])

        tr = entries[entries["date"].dt.normalize().isin(train_dates)].copy()
        te = entries[entries["date"].dt.normalize().isin(test_dates)].copy()
        if tr.empty or te.empty:
            continue

        frames = ex._build_symbol_frames(ds)
        baseline_train = ex._evaluate_entries(tr, frames, baseline_cfg)
        baseline_test = ex._evaluate_entries(te, frames, baseline_cfg)
        locked_train = ex._evaluate_entries(tr, frames, locked_cfg)
        locked_test = ex._evaluate_entries(te, frames, locked_cfg)

        best_cfg = None
        best_train = None
        for cfg in short_focus_candidates:
            m = ex._evaluate_entries(tr, frames, cfg)
            if m["trades"] <= 0:
                continue
            if best_train is None or m["score"] > best_train["score"]:
                best_train = m
                best_cfg = cfg
        if best_cfg is None or best_train is None:
            continue
        best_test = ex._evaluate_entries(te, frames, best_cfg)

        rows.append(
            {
                "symbol": sym,
                "bars": len(ds),
                "entries_total": len(entries),
                "entries_train": len(tr),
                "entries_test": len(te),
                "split_train_end": str(dts[split_idx - 1].date()),
                "split_test_start": str(dts[split_idx].date()),
                "baseline_cfg": json.dumps(baseline_cfg, sort_keys=True),
                "baseline_test_comp_ret_pct": 100.0 * float(baseline_test["comp_ret"]),
                "baseline_test_total_ret_pct": 100.0 * float(baseline_test["total_ret"]),
                "baseline_test_hit_rate_pct": 100.0 * float(baseline_test["hit_rate"]),
                "locked_cfg": json.dumps(locked_cfg, sort_keys=True),
                "locked_train_score": float(locked_train["score"]),
                "locked_test_comp_ret_pct": 100.0 * float(locked_test["comp_ret"]),
                "locked_test_total_ret_pct": 100.0 * float(locked_test["total_ret"]),
                "locked_test_hit_rate_pct": 100.0 * float(locked_test["hit_rate"]),
                "locked_edge_vs_baseline_comp_pct": 100.0
                * (float(locked_test["comp_ret"]) - float(baseline_test["comp_ret"])),
                "locked_edge_vs_baseline_total_pct": 100.0
                * (float(locked_test["total_ret"]) - float(baseline_test["total_ret"])),
                "opt_best_cfg": json.dumps(best_cfg, sort_keys=True),
                "opt_train_score": float(best_train["score"]),
                "opt_test_comp_ret_pct": 100.0 * float(best_test["comp_ret"]),
                "opt_test_total_ret_pct": 100.0 * float(best_test["total_ret"]),
                "opt_test_hit_rate_pct": 100.0 * float(best_test["hit_rate"]),
                "opt_edge_vs_baseline_comp_pct": 100.0
                * (float(best_test["comp_ret"]) - float(baseline_test["comp_ret"])),
                "opt_edge_vs_baseline_total_pct": 100.0
                * (float(best_test["total_ret"]) - float(baseline_test["total_ret"])),
                "opt_edge_vs_locked_comp_pct": 100.0
                * (float(best_test["comp_ret"]) - float(locked_test["comp_ret"])),
                "opt_edge_vs_locked_total_pct": 100.0
                * (float(best_test["total_ret"]) - float(locked_test["total_ret"])),
            }
        )

    if not rows:
        raise RuntimeError("No per-symbol rows generated.")
    out = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
    report_path = Path(args.save_report_csv)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(report_path, index=False)

    print("=== Short Symbol Holdout Report ===")
    print(f"candidate_profile={args.candidate_profile}")
    print(
        out[
            [
                "symbol",
                "entries_test",
                "baseline_test_total_ret_pct",
                "locked_test_total_ret_pct",
                "locked_edge_vs_baseline_total_pct",
                "opt_test_total_ret_pct",
                "opt_edge_vs_baseline_total_pct",
                "opt_edge_vs_locked_total_pct",
            ]
        ].to_string(index=False)
    )
    print(f"\nSaved report CSV: {report_path}")


if __name__ == "__main__":
    main()
