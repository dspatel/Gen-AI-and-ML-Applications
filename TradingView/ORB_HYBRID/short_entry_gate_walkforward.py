from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import exit_optimizer_walkforward as ex

DEFAULT_SYMBOLS_FILE = Path(__file__).resolve().parent / "universes" / "focus_symbols_v1.txt"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "alpaca_daily_cache.sqlite"
DEFAULT_FOLDS_CSV = Path(__file__).resolve().parent / "reports" / "short_entry_gate_folds.csv"
DEFAULT_SUMMARY_CSV = Path(__file__).resolve().parent / "reports" / "short_entry_gate_summary.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Walk-forward entry-gate optimizer for short breakouts. "
            "Exit is fixed to locked short preset; only entry filter is tuned on train and tested out-of-sample."
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

    p.add_argument("--train-days", type=int, default=180)
    p.add_argument("--test-days", type=int, default=60)
    p.add_argument("--step-days", type=int, default=60)
    p.add_argument("--min-trades-train", type=int, default=20)
    p.add_argument("--min-trades-test", type=int, default=5)

    p.add_argument("--save-folds-csv", default=str(DEFAULT_FOLDS_CSV))
    p.add_argument("--save-summary-csv", default=str(DEFAULT_SUMMARY_CSV))
    return p.parse_args()


def _locked_short_cfg() -> dict[str, Any]:
    return {
        "exit_type": "hybrid",
        "atr_mult": 1.5,
        "hard_stop_atr": 1.0,
        "breakeven_r": 0.0,
        "use_ema_flip": False,
        "max_hold": 15,
    }


def _enrich_symbol_frames(daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frames = ex._build_symbol_frames(daily)
    out: dict[str, pd.DataFrame] = {}
    for sym, f in frames.items():
        z = f.copy()
        z["ret5"] = z["close"].pct_change(5)
        z["ret20"] = z["close"].pct_change(20)
        z["atr_pct"] = z["atr14"] / z["close"]
        z["dist_ema20"] = z["close"] / z["ema20_d"] - 1.0
        z["vol_ratio20"] = z["volume"] / z["volume"].rolling(20, min_periods=5).mean()
        z["range20_pct"] = (
            z["high"].rolling(20, min_periods=20).max() - z["low"].rolling(20, min_periods=20).min()
        ) / z["close"]
        out[sym] = z
    return out


def _build_spy_context(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    f = frames.get("SPY")
    if f is None or f.empty:
        return pd.DataFrame(columns=["date", "spy_ret5", "spy_ret20", "spy_dist_ema20", "spy_atr_pct"])
    z = f.copy()
    z["spy_ret5"] = z["close"].pct_change(5)
    z["spy_ret20"] = z["close"].pct_change(20)
    z["spy_dist_ema20"] = z["close"] / z["ema20_d"] - 1.0
    z["spy_atr_pct"] = z["atr14"] / z["close"]
    return z[["date", "spy_ret5", "spy_ret20", "spy_dist_ema20", "spy_atr_pct"]].copy()


def _attach_entry_features(
    entries: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    spy_ctx: pd.DataFrame,
) -> pd.DataFrame:
    if entries.empty:
        return entries
    spy_map = spy_ctx.set_index("date").to_dict(orient="index") if not spy_ctx.empty else {}
    rows: list[dict[str, Any]] = []
    for r in entries.itertuples(index=False):
        sym = str(r.symbol)
        idx = int(r.idx)
        f = frames.get(sym)
        if f is None or idx < 0 or idx >= len(f):
            continue
        dt = f.at[idx, "date"]
        spy_vals = spy_map.get(dt, {})
        rows.append(
            {
                **r._asdict(),
                "ret5": f.at[idx, "ret5"],
                "ret20": f.at[idx, "ret20"],
                "atr_pct": f.at[idx, "atr_pct"],
                "dist_ema20": f.at[idx, "dist_ema20"],
                "vol_ratio20": f.at[idx, "vol_ratio20"],
                "range20_pct": f.at[idx, "range20_pct"],
                "spy_ret5": spy_vals.get("spy_ret5", np.nan),
                "spy_ret20": spy_vals.get("spy_ret20", np.nan),
                "spy_dist_ema20": spy_vals.get("spy_dist_ema20", np.nan),
                "spy_atr_pct": spy_vals.get("spy_atr_pct", np.nan),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["date", "symbol", "idx"]).reset_index(drop=True)


def _gate_candidates() -> list[dict[str, Any]]:
    return [
        {"name": "no_filter"},
        {"name": "dist_ema20_le_005", "max_dist_ema20": -0.005},
        {"name": "dist_ema20_le_010", "max_dist_ema20": -0.010},
        {"name": "ret5_le_005", "max_ret5": -0.005},
        {"name": "ret5_le_010", "max_ret5": -0.010},
        {"name": "ret20_le_020", "max_ret20": -0.020},
        {"name": "atr_pct_ge_012", "min_atr_pct": 0.012},
        {"name": "atr_pct_ge_018", "min_atr_pct": 0.018},
        {"name": "combo_dist005_ret5_005", "max_dist_ema20": -0.005, "max_ret5": -0.005},
        {"name": "combo_dist010_ret20_020", "max_dist_ema20": -0.010, "max_ret20": -0.020},
        {"name": "combo_ret5_010_atr012", "max_ret5": -0.010, "min_atr_pct": 0.012},
        {"name": "combo_dist005_atr012", "max_dist_ema20": -0.005, "min_atr_pct": 0.012},
        {"name": "spy_below_ema20", "max_spy_dist_ema20": 0.0},
        {"name": "spy_ret5_le_003", "max_spy_ret5": -0.003},
        {"name": "spy_ret20_le_01", "max_spy_ret20": -0.010},
        {"name": "combo_spy_below_and_dist005", "max_spy_dist_ema20": 0.0, "max_dist_ema20": -0.005},
        {"name": "combo_spy_ret5_and_ret5", "max_spy_ret5": -0.003, "max_ret5": -0.005},
    ]


def _apply_gate(entries: pd.DataFrame, gate: dict[str, Any]) -> pd.DataFrame:
    if entries.empty:
        return entries
    m = pd.Series(True, index=entries.index)
    if "max_dist_ema20" in gate:
        m &= entries["dist_ema20"] <= float(gate["max_dist_ema20"])
    if "max_ret5" in gate:
        m &= entries["ret5"] <= float(gate["max_ret5"])
    if "max_ret20" in gate:
        m &= entries["ret20"] <= float(gate["max_ret20"])
    if "min_atr_pct" in gate:
        m &= entries["atr_pct"] >= float(gate["min_atr_pct"])
    if "max_vol_ratio20" in gate:
        m &= entries["vol_ratio20"] <= float(gate["max_vol_ratio20"])
    if "max_range20_pct" in gate:
        m &= entries["range20_pct"] <= float(gate["max_range20_pct"])
    if "max_spy_dist_ema20" in gate:
        m &= entries["spy_dist_ema20"] <= float(gate["max_spy_dist_ema20"])
    if "max_spy_ret5" in gate:
        m &= entries["spy_ret5"] <= float(gate["max_spy_ret5"])
    if "max_spy_ret20" in gate:
        m &= entries["spy_ret20"] <= float(gate["max_spy_ret20"])
    return entries[m.fillna(False)].copy().reset_index(drop=True)


def _summarize_folds(folds: pd.DataFrame) -> pd.DataFrame:
    if folds.empty:
        return pd.DataFrame()
    total_edge = folds["best_test_total_ret_pct"] - folds["baseline_test_total_ret_pct"]
    avg_edge = folds["best_test_avg_ret_pct"] - folds["baseline_test_avg_ret_pct"]
    return pd.DataFrame(
        [
            {
                "folds": int(len(folds)),
                "median_best_test_comp_ret_pct": float(folds["best_test_comp_ret_pct"].median()),
                "median_baseline_test_comp_ret_pct": float(folds["baseline_test_comp_ret_pct"].median()),
                "mean_test_comp_edge_pct": float(folds["test_comp_edge_pct"].mean()),
                "median_test_comp_edge_pct": float(folds["test_comp_edge_pct"].median()),
                "mean_test_total_ret_edge_pct": float(total_edge.mean()),
                "median_test_total_ret_edge_pct": float(total_edge.median()),
                "mean_test_avg_ret_edge_pct": float(avg_edge.mean()),
                "median_test_avg_ret_edge_pct": float(avg_edge.median()),
                "win_folds_vs_baseline_pct": 100.0 * float(np.mean(folds["test_comp_edge_pct"] > 0.0)),
                "median_best_test_hit_rate_pct": float(folds["best_test_hit_rate_pct"].median()),
                "median_best_test_max_dd_pct": float(folds["best_test_max_dd_pct"].median()),
            }
        ]
    )


def main() -> None:
    args = _parse_args()
    symbols = ex._parse_symbols(args)
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Day"

    conn = sqlite3.connect(str(Path(args.db_path)))
    daily = ex._read_daily(conn, source_key=source_key, symbols=symbols)
    conn.close()
    if daily.empty:
        raise RuntimeError(f"No cached daily bars for source_key={source_key} and symbols={len(symbols)}.")

    entries = ex._build_entries(
        daily=daily,
        breakout_window=int(args.breakout_window),
        range_mode=str(args.range_mode),
        setup_max_days=int(args.setup_max_days),
        side_mode="short",
    )
    if entries.empty:
        raise RuntimeError("No short entries generated.")

    symbol_frames = _enrich_symbol_frames(daily)
    spy_ctx = _build_spy_context(symbol_frames)
    entries = _attach_entry_features(entries, symbol_frames, spy_ctx)
    if entries.empty:
        raise RuntimeError("No entries with features available.")

    locked_cfg = _locked_short_cfg()
    gates = _gate_candidates()
    gate_baseline = gates[0]

    calendar_dates = sorted(pd.to_datetime(daily["date"]).dt.normalize().unique().tolist())
    folds: list[dict[str, Any]] = []

    fold = 0
    i = 0
    while i + int(args.train_days) + int(args.test_days) <= len(calendar_dates):
        train_set = set(calendar_dates[i : i + int(args.train_days)])
        test_set = set(calendar_dates[i + int(args.train_days) : i + int(args.train_days) + int(args.test_days)])
        tr_all = entries[entries["date"].dt.normalize().isin(train_set)].copy()
        te_all = entries[entries["date"].dt.normalize().isin(test_set)].copy()

        if len(tr_all) >= int(args.min_trades_train) and len(te_all) >= int(args.min_trades_test):
            tr_base = _apply_gate(tr_all, gate_baseline)
            te_base = _apply_gate(te_all, gate_baseline)
            base_train = ex._evaluate_entries(tr_base, symbol_frames, locked_cfg)
            base_test = ex._evaluate_entries(te_base, symbol_frames, locked_cfg)

            best_gate = None
            best_train = None
            best_test = None
            for gate in gates:
                tr = _apply_gate(tr_all, gate)
                te = _apply_gate(te_all, gate)
                if len(tr) < int(args.min_trades_train) or len(te) < int(args.min_trades_test):
                    continue
                m_tr = ex._evaluate_entries(tr, symbol_frames, locked_cfg)
                if m_tr["trades"] < int(args.min_trades_train):
                    continue
                if best_train is None or m_tr["score"] > best_train["score"]:
                    best_gate = gate
                    best_train = m_tr
                    best_test = ex._evaluate_entries(te, symbol_frames, locked_cfg)

            if best_gate is not None and best_train is not None and best_test is not None:
                folds.append(
                    {
                        "fold": fold,
                        "train_start": calendar_dates[i],
                        "train_end": calendar_dates[i + int(args.train_days) - 1],
                        "test_start": calendar_dates[i + int(args.train_days)],
                        "test_end": calendar_dates[i + int(args.train_days) + int(args.test_days) - 1],
                        "train_entries_total": int(len(tr_all)),
                        "test_entries_total": int(len(te_all)),
                        "best_gate": json.dumps(best_gate, sort_keys=True),
                        "best_train_score": float(best_train["score"]),
                        "best_train_comp_ret_pct": 100.0 * float(best_train["comp_ret"]),
                        "best_test_comp_ret_pct": 100.0 * float(best_test["comp_ret"]),
                        "best_test_hit_rate_pct": 100.0 * float(best_test["hit_rate"]),
                        "best_test_avg_ret_pct": 100.0 * float(best_test["avg_ret"]),
                        "best_test_total_ret_pct": 100.0 * float(best_test["total_ret"]),
                        "best_test_max_dd_pct": 100.0 * float(best_test["max_dd"]),
                        "baseline_gate": json.dumps(gate_baseline, sort_keys=True),
                        "baseline_test_comp_ret_pct": 100.0 * float(base_test["comp_ret"]),
                        "baseline_test_hit_rate_pct": 100.0 * float(base_test["hit_rate"]),
                        "baseline_test_avg_ret_pct": 100.0 * float(base_test["avg_ret"]),
                        "baseline_test_total_ret_pct": 100.0 * float(base_test["total_ret"]),
                        "baseline_test_max_dd_pct": 100.0 * float(base_test["max_dd"]),
                        "test_comp_edge_pct": 100.0 * (float(best_test["comp_ret"]) - float(base_test["comp_ret"])),
                    }
                )
        fold += 1
        i += int(args.step_days)

    folds_df = pd.DataFrame(folds)
    if folds_df.empty:
        raise RuntimeError("No valid folds. Reduce min-trade thresholds or expand history.")
    summary_df = _summarize_folds(folds_df)

    print("=== Short Entry Gate Optimization ===")
    print(
        f"symbols={len(symbols)} bars={len(daily)} short_entries={len(entries)} "
        f"window={args.breakout_window} range_mode={args.range_mode}"
    )
    print(
        f"train_days={args.train_days} test_days={args.test_days} step_days={args.step_days} "
        f"min_train={args.min_trades_train} min_test={args.min_trades_test}"
    )
    print(f"locked_exit_cfg={locked_cfg}")
    print(f"gate_candidates={len(gates)}")

    print("\n=== Fold Results (head) ===")
    print(
        folds_df[
            [
                "fold",
                "train_start",
                "train_end",
                "test_start",
                "test_end",
                "best_gate",
                "best_test_comp_ret_pct",
                "baseline_test_comp_ret_pct",
                "test_comp_edge_pct",
            ]
        ]
        .head(12)
        .to_string(index=False)
    )

    print("\n=== Summary ===")
    print(summary_df.to_string(index=False))

    folds_path = Path(args.save_folds_csv)
    summary_path = Path(args.save_summary_csv)
    folds_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    folds_df.to_csv(folds_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    print(f"\nSaved folds CSV: {folds_path}")
    print(f"Saved summary CSV: {summary_path}")


if __name__ == "__main__":
    main()
