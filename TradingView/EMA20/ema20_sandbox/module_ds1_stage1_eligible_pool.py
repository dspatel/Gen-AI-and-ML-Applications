from __future__ import annotations

import os
import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def main():
    root = os.getcwd()

    ranked_path = os.path.join(root, "data", "research", "universe_ranked.csv")
    out_dir = os.path.join(root, "data", "research", "dynamic_selection")
    ensure_dir(out_dir)

    ranked = load_csv(ranked_path)
    if ranked.empty:
        raise SystemExit(f"Missing/empty: {ranked_path} (run Module S6 first)")

    ranked["symbol"] = ranked["symbol"].astype(str).str.upper()

    # ---- Stage-1 scoring (stable, slow) ----
    # For now: rely primarily on compatibility_score (EMA structure)
    # You can later blend in daily-context features once we add a daily-bars source.
    if "compatibility_score" not in ranked.columns:
        raise SystemExit("universe_ranked.csv is missing compatibility_score. Re-run Module S6.")

    ranked["stage1_score"] = ranked["compatibility_score"]

    # Config: choose Eligible Pool size
    eligible_pool_size = 50

    eligible = ranked.sort_values("stage1_score", ascending=False).head(eligible_pool_size).copy()

    # Keep a simple schema for downstream Stage-2
    keep_cols = []
    for c in ["symbol", "stage1_score", "final_rank_score", "compatibility_score", "opportunity_score"]:
        if c in eligible.columns:
            keep_cols.append(c)

    eligible = eligible[keep_cols].copy()

    out_path = os.path.join(out_dir, "eligible_pool.csv")
    eligible.to_csv(out_path, index=False)

    print("\n=== DS1 Stage-1 Eligible Pool ===")
    print(f"Eligible pool size: {eligible_pool_size}")
    print("Saved:", out_path)
    print("\nTop 20 eligible by stage1_score:")
    print(eligible.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
