from __future__ import annotations

import os
from typing import Optional, Dict, Any
import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    if os.path.getsize(path) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()



def profit_factor(trades: pd.DataFrame) -> Optional[float]:
    if trades.empty:
        return None
    gp = trades.loc[trades["gross_pnl_per_share"] > 0, "gross_pnl_per_share"].sum()
    gl = trades.loc[trades["gross_pnl_per_share"] < 0, "gross_pnl_per_share"].abs().sum()
    if gl == 0:
        return None
    return float(gp / gl)


def summarize(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades.empty:
        return {"trades": 0, "total_r": 0.0, "win_rate": None, "profit_factor": None}
    n = int(len(trades))
    wins = int((trades["r_multiple"] > 0).sum())
    return {
        "trades": n,
        "total_r": float(trades["r_multiple"].sum()),
        "win_rate": float(wins / n) if n else None,
        "profit_factor": profit_factor(trades),
    }


def main():
    base_con = os.path.join(os.getcwd(), "data", "research", "conservative")
    base_fb = os.path.join(os.getcwd(), "data", "research", "orb_fallback")
    out_dir = os.path.join(os.getcwd(), "data", "research", "ab_tests")
    os.makedirs(out_dir, exist_ok=True)

    symbols = sorted([d for d in os.listdir(base_con) if os.path.isdir(os.path.join(base_con, d)) and d not in {"ab_tests"}])

    rows = []
    for sym in symbols:
        con_tr = load_csv(os.path.join(base_con, sym, "trades.csv"))
        fb_tr = load_csv(os.path.join(base_fb, sym, "trades.csv"))

        con_s = summarize(con_tr)
        fb_s = summarize(fb_tr)

        # Combined view (baseline trades + fallback trades)
        combined = pd.concat([con_tr, fb_tr], ignore_index=True) if (not con_tr.empty or not fb_tr.empty) else pd.DataFrame()
        comb_s = summarize(combined)

        rows.append({
            "symbol": sym,
            "baseline_trades": con_s["trades"],
            "baseline_total_r": con_s["total_r"],
            "baseline_win_rate": con_s["win_rate"],
            "baseline_profit_factor": con_s["profit_factor"],

            "fallback_trades_added": fb_s["trades"],
            "fallback_total_r": fb_s["total_r"],
            "fallback_win_rate": fb_s["win_rate"],
            "fallback_profit_factor": fb_s["profit_factor"],

            "combined_trades": comb_s["trades"],
            "combined_total_r": comb_s["total_r"],
            "combined_win_rate": comb_s["win_rate"],
            "combined_profit_factor": comb_s["profit_factor"],
        })

    out = pd.DataFrame(rows)
    out_path = os.path.join(out_dir, "v3_orb_fallback_summary.csv")
    out.to_csv(out_path, index=False)

    print("✅ Saved:", out_path)
    if not out.empty:
        print("\nTop 10 symbols by fallback trades added:")
        print(out.sort_values("fallback_trades_added", ascending=False).head(10)[
            ["symbol", "fallback_trades_added", "fallback_total_r", "combined_total_r", "combined_profit_factor"]
        ])


if __name__ == "__main__":
    main()
