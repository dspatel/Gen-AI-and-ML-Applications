# ============================================================
# Module V1: Opportunity Scorecard
#
# Purpose:
#   Evaluate opportunity capture independently of PnL
#
# Evaluates at multiple R thresholds (e.g. 0.5R, 1.0R)
#
# Inputs (per symbol):
#   decision_vs_reality.csv
#   trades.csv
#
# Outputs:
#   opportunity_scorecard.csv
#   opportunity_scorecard_universe.csv
# ============================================================

from __future__ import annotations
import os
import pandas as pd
from typing import List, Dict

R_LEVELS = [0.5, 1.0]  # extensible


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path)


def compute_scorecard(
    symbol: str,
    decision_df: pd.DataFrame,
    trades_df: pd.DataFrame
) -> List[Dict]:
    rows = []

    for r in R_LEVELS:
        opp = decision_df.copy()

        # define opportunity at R
        opp["is_opportunity"] = opp["future_mfe_r"] >= r
        opp["is_adverse"] = opp["future_mae_r"] <= -r

        total_opps = int(opp["is_opportunity"].sum())

        traded_sessions = set(trades_df["session_date"]) if not trades_df.empty else set()

        opp["traded"] = opp["session_date"].isin(traded_sessions)

        traded_opps = int((opp["is_opportunity"] & opp["traded"]).sum())
        missed_opps = int((opp["is_opportunity"] & ~opp["traded"]).sum())

        wrong_trades = int((opp["is_adverse"] & opp["traded"]).sum())
        correct_skips = int((opp["is_adverse"] & ~opp["traded"]).sum())

        total_trades = len(traded_sessions)

        recall = traded_opps / total_opps if total_opps > 0 else None
        precision = traded_opps / total_trades if total_trades > 0 else None

        avg_mfe_traded = opp.loc[opp["traded"], "future_mfe_r"].mean()
        avg_mfe_skipped = opp.loc[~opp["traded"], "future_mfe_r"].mean()

        rows.append({
            "symbol": symbol,
            "r_threshold": r,
            "total_opportunities": total_opps,
            "traded_opportunities": traded_opps,
            "missed_opportunities": missed_opps,
            "total_trades": total_trades,
            "recall": recall,
            "precision": precision,
            "wrong_trades": wrong_trades,
            "correct_skips": correct_skips,
            "avg_future_mfe_traded": avg_mfe_traded,
            "avg_future_mfe_skipped": avg_mfe_skipped,
        })

    return rows


def main():
    base_dir = os.path.join(os.getcwd(), "data", "research", "conservative")
    universe_rows = []

    for symbol in os.listdir(base_dir):
        sym_dir = os.path.join(base_dir, symbol)
        if not os.path.isdir(sym_dir):
            continue

        decision_path = os.path.join(sym_dir, "decision_vs_reality.csv")
        trades_path = os.path.join(sym_dir, "trades.csv")

        decision_df = load_csv(decision_path)
        trades_df = load_csv(trades_path)

        if decision_df.empty:
            continue

        rows = compute_scorecard(symbol, decision_df, trades_df)
        scorecard = pd.DataFrame(rows)

        out_path = os.path.join(sym_dir, "opportunity_scorecard.csv")
        scorecard.to_csv(out_path, index=False)

        universe_rows.extend(rows)

        print(f"✅ {symbol}: opportunity scorecard saved")

    universe_df = pd.DataFrame(universe_rows)
    universe_out = os.path.join(base_dir, "opportunity_scorecard_universe.csv")
    universe_df.to_csv(universe_out, index=False)

    print("\n✅ Universe opportunity scorecard saved")
    print(universe_df.sort_values(["r_threshold", "recall"], ascending=[True, False]).head(10))


if __name__ == "__main__":
    main()
