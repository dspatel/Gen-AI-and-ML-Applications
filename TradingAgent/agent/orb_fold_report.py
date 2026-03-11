from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd


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
    pnl_r = df["r_mult"].astype(float)
    gp = pnl_r[pnl_r > 0].sum()
    gl = -pnl_r[pnl_r < 0].sum()
    pf = float(gp / gl) if gl > 0 else float("inf")
    eq = (1.0 + df["ret_pct"].astype(float)).cumprod()
    dd = (eq / eq.cummax() - 1.0).min() * 100.0
    return {
        "trades": int(len(df)),
        "ret_pct": float(((1.0 + df["ret_pct"].astype(float)).prod() - 1.0) * 100.0),
        "avg_r": float(pnl_r.mean()),
        "win_rate": float((pnl_r > 0).mean()),
        "pf": pf,
        "max_dd_pct": float(abs(dd)),
    }


def _select_strategy(trades: pd.DataFrame, train_years: list[int], min_train_trades: int) -> str:
    train = trades[trades["year"].isin(train_years)].copy()
    if train.empty:
        raise ValueError(f"No training trades for train_years={train_years}")

    rows: list[dict] = []
    for strategy_id, grp in train.groupby("strategy_id"):
        yrets: list[float] = []
        for y in train_years:
            gy = grp[grp["year"] == y]
            yret = float(((1.0 + gy["ret_pct"].astype(float)).prod() - 1.0) * 100.0) if len(gy) else float("-inf")
            yrets.append(yret)
        m = _perf(grp)
        rows.append(
            {
                "strategy_id": strategy_id,
                "train_trades": m["trades"],
                "train_ret_pct": m["ret_pct"],
                "train_pf": m["pf"],
                "train_max_dd_pct": m["max_dd_pct"],
                "train_min_year_ret_pct": min(yrets),
                "train_pos_years": int(sum(1 for x in yrets if x > 0)),
            }
        )

    rank = pd.DataFrame(rows)
    eligible = rank[rank["train_trades"] >= int(min_train_trades)].copy()
    if not eligible.empty:
        rank = eligible
    rank = rank.sort_values(
        ["train_pos_years", "train_min_year_ret_pct", "train_pf", "train_ret_pct", "train_max_dd_pct"],
        ascending=[False, False, False, False, True],
    )
    return str(rank.iloc[0]["strategy_id"])


def _load_trades(db_path: str, run_id: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        trades = pd.read_sql_query(
            """
            SELECT strategy_id, session_date, r_mult, ret_pct
            FROM trades
            WHERE run_id=?
            """,
            conn,
            params=[run_id],
        )
    finally:
        conn.close()
    if trades.empty:
        return trades
    trades["session_date"] = pd.to_datetime(trades["session_date"])
    trades["year"] = trades["session_date"].dt.year
    return trades


def resolve_run_id(db_path: str, run_id: str | None = None, symbol: str | None = None) -> str:
    if run_id:
        return str(run_id)
    conn = sqlite3.connect(db_path)
    try:
        if symbol:
            row = conn.execute(
                """
                SELECT run_id
                FROM strategy_runs
                WHERE mode='orb' AND status='completed' AND symbol=?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT run_id
                FROM strategy_runs
                WHERE mode='orb' AND status='completed'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError("No completed ORB runs found. Provide --run-id explicitly.")
    return str(row[0])


def build_fold_report(
    db_path: str,
    run_id: str,
    locked_strategy_id: str,
    output_csv: str,
    min_train_trades: int = 200,
) -> pd.DataFrame:
    trades = _load_trades(db_path=db_path, run_id=run_id)
    if trades.empty:
        raise ValueError(f"No trades found for run_id={run_id}")

    years = sorted(int(y) for y in trades["year"].dropna().unique().tolist())
    if len(years) < 2:
        raise ValueError(f"Need at least 2 years to build folds. Available years={years}")

    out_rows: list[dict] = []
    for i in range(1, len(years)):
        train_years = years[:i]
        test_year = years[i]
        selected = _select_strategy(trades, train_years, min_train_trades=min_train_trades)

        for mode, strategy_id in [("fold_selected", selected), ("locked_strategy", locked_strategy_id)]:
            g = trades[(trades["strategy_id"] == strategy_id) & (trades["year"] == test_year)].copy()
            out_rows.append(
                {
                    "mode": mode,
                    "train_years": "-".join(map(str, train_years)),
                    "test_year": test_year,
                    "strategy_id": strategy_id,
                    **_perf(g),
                }
            )

    out = pd.DataFrame(out_rows).sort_values(["test_year", "mode"]).reset_index(drop=True)
    p = Path(output_csv)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False)
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build yearly walk-forward fold report for an ORB research run.")
    parser.add_argument("--db-path", default="./orb_research.db")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--symbol", default=None, help="Optional symbol to auto-resolve latest run when --run-id is omitted.")
    parser.add_argument("--locked-strategy-id", required=True)
    parser.add_argument("--min-train-trades", type=int, default=200)
    parser.add_argument("--output-csv", default="./artifacts/orb_research/orb_yearly_folds.csv")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_id = resolve_run_id(db_path=args.db_path, run_id=args.run_id, symbol=args.symbol)
    out = build_fold_report(
        db_path=args.db_path,
        run_id=run_id,
        locked_strategy_id=args.locked_strategy_id,
        output_csv=args.output_csv,
        min_train_trades=args.min_train_trades,
    )
    print(f"run_id={run_id}")
    print(out.to_string(index=False))
    print(f"saved={args.output_csv}")


if __name__ == "__main__":
    main()
