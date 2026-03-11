from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _wavg(values: pd.Series, weights: pd.Series) -> float:
    w = float(weights.sum())
    if w <= 0:
        return 0.0
    return float((values * weights).sum() / w)


def build_gap_fold_report(
    yearly_csv: str,
    output_csv: str,
    output_json: str,
    base_model: str = "base_limit1",
    combo_model: str = "combo",
) -> tuple[pd.DataFrame, dict]:
    inp = Path(yearly_csv)
    if not inp.exists():
        raise FileNotFoundError(f"Missing yearly CSV: {yearly_csv}")

    raw = pd.read_csv(inp)
    need = {"year", "trades", "year_return_pct", "symbol", "model"}
    missing = need - set(raw.columns)
    if missing:
        raise ValueError(f"yearly CSV missing required columns: {sorted(missing)}")

    df = raw.copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["model"] = df["model"].astype(str).str.strip().str.lower()
    df["year"] = df["year"].astype(int)
    df["trades"] = df["trades"].astype(float).fillna(0.0).astype(int)
    df["year_return_pct"] = df["year_return_pct"].astype(float).fillna(0.0)

    df = df[df["model"].isin({base_model.lower(), combo_model.lower()})].copy()
    if df.empty:
        raise ValueError("No rows found for requested base/combo models")

    years = sorted(df["year"].unique().tolist())
    symbols = sorted(df["symbol"].unique().tolist())
    if len(years) < 2:
        raise ValueError(f"Need at least 2 years for folds. Available years: {years}")

    idx = {
        (str(r.symbol), int(r.year), str(r.model)): r
        for r in df.itertuples(index=False)
    }

    fold_rows: list[dict] = []
    for test_year in years:
        train_years = [y for y in years if y < test_year]
        if not train_years:
            continue
        for sym in symbols:
            train_base = [
                float(idx[(sym, y, base_model.lower())].year_return_pct)
                for y in train_years
                if (sym, y, base_model.lower()) in idx
            ]
            train_combo = [
                float(idx[(sym, y, combo_model.lower())].year_return_pct)
                for y in train_years
                if (sym, y, combo_model.lower()) in idx
            ]
            if not train_base or not train_combo:
                continue

            train_base_mean = float(sum(train_base) / len(train_base))
            train_combo_mean = float(sum(train_combo) / len(train_combo))
            selected = combo_model.lower() if train_combo_mean > train_base_mean else base_model.lower()

            skey = (sym, test_year, selected)
            bkey = (sym, test_year, base_model.lower())
            ckey = (sym, test_year, combo_model.lower())
            if skey not in idx or bkey not in idx or ckey not in idx:
                continue

            s = idx[skey]
            b = idx[bkey]
            c = idx[ckey]
            fold_rows.append(
                {
                    "symbol": sym,
                    "test_year": int(test_year),
                    "train_years": "-".join(map(str, train_years)),
                    "selected_model": selected,
                    "train_base_mean_ret_pct": train_base_mean,
                    "train_combo_mean_ret_pct": train_combo_mean,
                    "test_selected_ret_pct": float(s.year_return_pct),
                    "test_selected_trades": int(s.trades),
                    "test_base_ret_pct": float(b.year_return_pct),
                    "test_base_trades": int(b.trades),
                    "test_combo_ret_pct": float(c.year_return_pct),
                    "test_combo_trades": int(c.trades),
                    "uplift_vs_base_pct": float(s.year_return_pct) - float(b.year_return_pct),
                    "uplift_vs_combo_pct": float(s.year_return_pct) - float(c.year_return_pct),
                }
            )

    if not fold_rows:
        raise ValueError("No fold rows generated")

    detail = pd.DataFrame(fold_rows).sort_values(["test_year", "symbol"]).reset_index(drop=True)
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    detail.to_csv(output_csv, index=False)

    years_out = sorted(detail["test_year"].unique().tolist())
    ew: dict[str, dict] = {}
    tw: dict[str, dict] = {}
    for y in years_out:
        g = detail[detail["test_year"] == y].copy()
        ew[str(y)] = {
            "selected_mean": float(g["test_selected_ret_pct"].mean()),
            "base_mean": float(g["test_base_ret_pct"].mean()),
            "combo_mean": float(g["test_combo_ret_pct"].mean()),
            "selected_median": float(g["test_selected_ret_pct"].median()),
            "base_median": float(g["test_base_ret_pct"].median()),
            "combo_median": float(g["test_combo_ret_pct"].median()),
            "selected_gt_base_rows": int((g["test_selected_ret_pct"] > g["test_base_ret_pct"]).sum()),
            "selected_ge_base_rows": int((g["test_selected_ret_pct"] >= g["test_base_ret_pct"]).sum()),
            "n": int(len(g)),
        }
        tw[str(y)] = {
            "selected_mean": _wavg(g["test_selected_ret_pct"], g["test_selected_trades"]),
            "base_mean": _wavg(g["test_base_ret_pct"], g["test_base_trades"]),
            "combo_mean": _wavg(g["test_combo_ret_pct"], g["test_combo_trades"]),
            "selected_trades": int(g["test_selected_trades"].sum()),
            "base_trades": int(g["test_base_trades"].sum()),
            "combo_trades": int(g["test_combo_trades"].sum()),
        }

    ew["all"] = {
        "selected_mean": float(detail["test_selected_ret_pct"].mean()),
        "base_mean": float(detail["test_base_ret_pct"].mean()),
        "combo_mean": float(detail["test_combo_ret_pct"].mean()),
        "selected_median": float(detail["test_selected_ret_pct"].median()),
        "base_median": float(detail["test_base_ret_pct"].median()),
        "combo_median": float(detail["test_combo_ret_pct"].median()),
        "selected_minus_base_mean": float(detail["test_selected_ret_pct"].mean() - detail["test_base_ret_pct"].mean()),
        "combo_minus_base_mean": float(detail["test_combo_ret_pct"].mean() - detail["test_base_ret_pct"].mean()),
        "selected_gt_base_rows": int((detail["test_selected_ret_pct"] > detail["test_base_ret_pct"]).sum()),
        "selected_ge_base_rows": int((detail["test_selected_ret_pct"] >= detail["test_base_ret_pct"]).sum()),
        "combo_gt_base_rows": int((detail["test_combo_ret_pct"] > detail["test_base_ret_pct"]).sum()),
        "n": int(len(detail)),
    }
    tw["all"] = {
        "selected_mean": _wavg(detail["test_selected_ret_pct"], detail["test_selected_trades"]),
        "base_mean": _wavg(detail["test_base_ret_pct"], detail["test_base_trades"]),
        "combo_mean": _wavg(detail["test_combo_ret_pct"], detail["test_combo_trades"]),
        "selected_trades": int(detail["test_selected_trades"].sum()),
        "base_trades": int(detail["test_base_trades"].sum()),
        "combo_trades": int(detail["test_combo_trades"].sum()),
    }

    global_policy: list[dict] = []
    for y in years_out:
        train = detail[detail["test_year"] < y].copy()
        test = detail[detail["test_year"] == y].copy()
        if train.empty or test.empty:
            continue
        train_base_mean = float(train["test_base_ret_pct"].mean())
        train_combo_mean = float(train["test_combo_ret_pct"].mean())
        choose = combo_model.lower() if train_combo_mean > train_base_mean else base_model.lower()
        test_sel_mean = float(test["test_combo_ret_pct"].mean()) if choose == combo_model.lower() else float(test["test_base_ret_pct"].mean())
        global_policy.append(
            {
                "test_year": int(y),
                "choose": choose,
                "train_base_mean": train_base_mean,
                "train_combo_mean": train_combo_mean,
                "test_selected_mean": test_sel_mean,
                "test_base_mean": float(test["test_base_ret_pct"].mean()),
                "test_combo_mean": float(test["test_combo_ret_pct"].mean()),
            }
        )

    summary = {
        "rows": int(len(detail)),
        "years": years_out,
        "models": {"base": base_model.lower(), "combo": combo_model.lower()},
        "equal_weight": ew,
        "trade_weighted": tw,
        "global_policy": global_policy,
        "artifacts": {
            "input_yearly_csv": str(inp).replace("\\", "/"),
            "detail_csv": str(Path(output_csv)).replace("\\", "/"),
            "summary_json": str(Path(output_json)).replace("\\", "/"),
        },
    }
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return detail, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate ORB gap add-on with prior-data-only yearly folds.")
    parser.add_argument("--yearly-csv", default="./orb_gap15_universe_yearly.csv")
    parser.add_argument("--output-csv", default="./artifacts/reports/orb_gap_fold_decision.csv")
    parser.add_argument("--output-json", default="./artifacts/reports/orb_gap_fold_summary.json")
    parser.add_argument("--base-model", default="base_limit1")
    parser.add_argument("--combo-model", default="combo")
    args = parser.parse_args()

    detail, summary = build_gap_fold_report(
        yearly_csv=args.yearly_csv,
        output_csv=args.output_csv,
        output_json=args.output_json,
        base_model=args.base_model,
        combo_model=args.combo_model,
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "rows": int(len(detail)),
                "output_csv": args.output_csv,
                "output_json": args.output_json,
                "selected_minus_base_mean": summary["equal_weight"]["all"]["selected_minus_base_mean"],
                "combo_minus_base_mean": summary["equal_weight"]["all"]["combo_minus_base_mean"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

