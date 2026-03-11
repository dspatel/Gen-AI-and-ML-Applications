from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .db import connect


def _perf(df: pd.DataFrame) -> dict:
    if df.empty:
        return {
            "trades": 0,
            "ret_pct": 0.0,
            "avg_r": 0.0,
            "win_rate": 0.0,
            "pf": 0.0,
            "max_dd_pct": 0.0,
        }
    pnl = df["r_mult"].astype(float)
    gp = pnl[pnl > 0].sum()
    gl = -pnl[pnl < 0].sum()
    pf = float(gp / gl) if gl > 0 else float("inf")
    eq = (1.0 + df["ret_pct"]).cumprod()
    dd = (eq / eq.cummax() - 1.0).min() * 100.0
    return {
        "trades": int(len(df)),
        "ret_pct": float(((1.0 + df["ret_pct"]).prod() - 1.0) * 100.0),
        "avg_r": float(pnl.mean()),
        "win_rate": float((pnl > 0).mean()),
        "pf": pf,
        "max_dd_pct": float(abs(dd)),
    }


def _select_variant(trades: pd.DataFrame, train_years: list[int]) -> str:
    train = trades[trades["year"].isin(train_years)].copy()
    if train.empty:
        raise ValueError(f"No training trades for train_years={train_years}")
    rows: list[dict] = []
    for variant_id, grp in train.groupby("variant_id"):
        yrets = []
        for y in train_years:
            gy = grp[grp["year"] == y]
            yret = float(((1.0 + gy["ret_pct"]).prod() - 1.0) * 100.0) if len(gy) else float("-inf")
            yrets.append(yret)
        m = _perf(grp)
        rows.append(
            {
                "variant_id": variant_id,
                "train_trades": m["trades"],
                "train_ret_pct": m["ret_pct"],
                "train_pf": m["pf"],
                "train_max_dd_pct": m["max_dd_pct"],
                "train_min_year_ret_pct": min(yrets),
                "train_pos_years": int(sum(1 for x in yrets if x > 0)),
            }
        )
    rank = pd.DataFrame(rows)
    rank = rank[rank["train_trades"] >= 200].copy()
    if rank.empty:
        rank = pd.DataFrame(rows)
    rank = rank.sort_values(
        ["train_pos_years", "train_min_year_ret_pct", "train_pf", "train_ret_pct", "train_max_dd_pct"],
        ascending=[False, False, False, False, True],
    )
    return str(rank.iloc[0]["variant_id"])


def build_fold_report(
    db_path: str,
    run_id: str,
    locked_variant: str,
    output_csv: str,
) -> pd.DataFrame:
    conn = connect(db_path)
    trades = pd.read_sql_query(
        """
        SELECT variant_id, session_date, r_mult, ret_pct
        FROM r6_trades
        WHERE run_id=?
        """,
        conn,
        params=[run_id],
    )
    conn.close()
    if trades.empty:
        raise ValueError(f"No trades found for run_id={run_id}")

    trades["session_date"] = pd.to_datetime(trades["session_date"])
    trades["year"] = trades["session_date"].dt.year

    folds = [
        ([2023], 2024),
        ([2023, 2024], 2025),
        ([2023, 2024, 2025], 2026),
    ]
    available_years = set(int(y) for y in trades["year"].unique().tolist())

    out_rows: list[dict] = []
    for train_years, test_year in folds:
        if test_year not in available_years:
            continue
        if not set(train_years).issubset(available_years):
            continue
        selected = _select_variant(trades, train_years)

        for mode, variant_id in [
            ("fold_selected", selected),
            ("locked_strategy", locked_variant),
        ]:
            g = trades[(trades["variant_id"] == variant_id) & (trades["year"] == test_year)].copy()
            out_rows.append(
                {
                    "mode": mode,
                    "train_years": "-".join(map(str, train_years)),
                    "test_year": test_year,
                    "variant_id": variant_id,
                    **_perf(g),
                }
            )

    if not out_rows:
        raise ValueError("No compatible folds for this run window. Need run coverage that includes fold train/test years.")

    out = pd.DataFrame(out_rows)
    out = out.sort_values(["test_year", "mode"])
    p = Path(output_csv)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False)
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build yearly walk-forward fold report for an R6 research run.")
    parser.add_argument("--db-path", default="./artifacts/orb_r6/orb_core.sqlite")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--locked-variant", required=True)
    parser.add_argument("--output-csv", default="./artifacts/orb_r6/research/r6_yearly_folds.csv")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    out = build_fold_report(
        db_path=args.db_path,
        run_id=args.run_id,
        locked_variant=args.locked_variant,
        output_csv=args.output_csv,
    )
    print(out.to_string(index=False))
    print(f"saved={args.output_csv}")


if __name__ == "__main__":
    main()
