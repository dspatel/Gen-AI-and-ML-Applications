from __future__ import annotations

import os
from typing import Dict, Any, Optional
import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df


def profit_factor(trades: pd.DataFrame) -> Optional[float]:
    if trades.empty:
        return None
    gp = trades.loc[trades["gross_pnl_per_share"] > 0, "gross_pnl_per_share"].sum()
    gl = trades.loc[trades["gross_pnl_per_share"] < 0, "gross_pnl_per_share"].abs().sum()
    if gl == 0:
        return None
    return float(gp / gl)


def summarize_trades(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades.empty:
        return {
            "trades": 0,
            "total_r": 0.0,
            "win_rate": None,
            "profit_factor": None,
        }
    n = int(len(trades))
    wins = int((trades["r_multiple"] > 0).sum())
    return {
        "trades": n,
        "total_r": float(trades["r_multiple"].sum()),
        "win_rate": float(wins / n) if n else None,
        "profit_factor": profit_factor(trades),
    }


def side_matches_orb(orb_dir: str, side: str) -> bool:
    # orb_dir UP -> LONG, DOWN -> SHORT
    if orb_dir == "UP" and side == "LONG":
        return True
    if orb_dir == "DOWN" and side == "SHORT":
        return True
    return False


def main():
    base_con = os.path.join(os.getcwd(), "data", "research", "conservative")
    base_ema = os.path.join(os.getcwd(), "data", "research", "ema_only")
    out_dir = os.path.join(os.getcwd(), "data", "research", "ab_tests")
    os.makedirs(out_dir, exist_ok=True)

    symbols = sorted([d for d in os.listdir(base_con) if os.path.isdir(os.path.join(base_con, d)) and d not in {"ab_tests"}])

    rows = []
    for sym in symbols:
        # ORB-gated baseline files
        con_dir = os.path.join(base_con, sym)
        con_dvr = load_csv(os.path.join(con_dir, "decision_vs_reality.csv"))
        con_trades = load_csv(os.path.join(con_dir, "trades.csv"))

        # EMA-only variant files
        ema_dir = os.path.join(base_ema, sym)
        ema_trades = load_csv(os.path.join(ema_dir, "trades.csv"))

        if con_dvr.empty:
            continue

        # Clean types
        for df in [con_dvr, con_trades, ema_trades]:
            if not df.empty and "session_date" in df.columns:
                df["session_date"] = df["session_date"].astype(str)

        # Define ORB opportunities at 1R using decision-vs-reality file
        # Opportunity day = future_mfe_r >= 1.0 in ORB direction (as defined in that file)
        con_dvr["is_1r_opp"] = con_dvr["future_mfe_r"] >= 1.0

        total_opp_days = int(con_dvr["is_1r_opp"].sum())

        # Baseline capture: did ORB-gated take a trade that session?
        con_trade_days = set(con_trades["session_date"]) if not con_trades.empty else set()
        con_dvr["con_traded"] = con_dvr["session_date"].isin(con_trade_days)

        con_traded_opps = int((con_dvr["is_1r_opp"] & con_dvr["con_traded"]).sum())
        con_recall = (con_traded_opps / total_opp_days) if total_opp_days else None

        # EMA-only capture: did EMA-only take a trade that session AND align with ORB direction?
        ema_trade_days = set(ema_trades["session_date"]) if not ema_trades.empty else set()
        ema_by_day = {}
        if not ema_trades.empty:
            # one trade per day expected
            for _, r in ema_trades.iterrows():
                ema_by_day[str(r["session_date"])] = str(r["side"]).upper()

        ema_aligned_trade = []
        for _, r in con_dvr.iterrows():
            sd = str(r["session_date"])
            orb_dir = str(r["thesis_direction"])  # UP/DOWN
            took = sd in ema_trade_days
            if not took:
                ema_aligned_trade.append(False)
            else:
                side = ema_by_day.get(sd, "")
                ema_aligned_trade.append(side_matches_orb(orb_dir, side))

        con_dvr["ema_traded_aligned"] = ema_aligned_trade

        ema_traded_opps_aligned = int((con_dvr["is_1r_opp"] & con_dvr["ema_traded_aligned"]).sum())
        ema_recall_aligned = (ema_traded_opps_aligned / total_opp_days) if total_opp_days else None

        # How many 1R opp days did EMA-only capture that ORB-gated missed?
        ema_only_wins_vs_gate = int((con_dvr["is_1r_opp"] & con_dvr["ema_traded_aligned"] & ~con_dvr["con_traded"]).sum())

        # Trade summaries (separate from opportunity capture)
        con_trade_summary = summarize_trades(con_trades)
        ema_trade_summary = summarize_trades(ema_trades)

        rows.append({
            "symbol": sym,

            # Opportunity-based (ORB-defined) evaluation
            "orb_defined_1r_opportunity_days": total_opp_days,
            "orb_gated_traded_opps": con_traded_opps,
            "orb_gated_recall_on_orb_opps": con_recall,

            "ema_only_aligned_traded_opps": ema_traded_opps_aligned,
            "ema_only_aligned_recall_on_orb_opps": ema_recall_aligned,

            "ema_only_captured_opps_gate_missed": ema_only_wins_vs_gate,

            # Trade summaries
            "orb_gated_trades": con_trade_summary["trades"],
            "orb_gated_total_r": con_trade_summary["total_r"],
            "orb_gated_win_rate": con_trade_summary["win_rate"],
            "orb_gated_profit_factor": con_trade_summary["profit_factor"],

            "ema_only_trades": ema_trade_summary["trades"],
            "ema_only_total_r": ema_trade_summary["total_r"],
            "ema_only_win_rate": ema_trade_summary["win_rate"],
            "ema_only_profit_factor": ema_trade_summary["profit_factor"],
        })

    out = pd.DataFrame(rows)
    out_path = os.path.join(out_dir, "orb_vs_ema_only_summary.csv")
    out.to_csv(out_path, index=False)

    print("\n✅ A/B Summary saved:", out_path)

    if not out.empty:
        print("\nTop 10 symbols where EMA-only captured ORB opportunities that ORB-gated missed:")
        print(out.sort_values("ema_only_captured_opps_gate_missed", ascending=False).head(10)[
            ["symbol", "ema_only_captured_opps_gate_missed", "orb_defined_1r_opportunity_days",
             "orb_gated_recall_on_orb_opps", "ema_only_aligned_recall_on_orb_opps"]
        ])

        print("\nTop 10 by EMA-only aligned recall on ORB opportunities:")
        print(out.sort_values("ema_only_aligned_recall_on_orb_opps", ascending=False).head(10)[
            ["symbol", "ema_only_aligned_recall_on_orb_opps", "orb_gated_recall_on_orb_opps",
             "ema_only_captured_opps_gate_missed"]
        ])


if __name__ == "__main__":
    main()
