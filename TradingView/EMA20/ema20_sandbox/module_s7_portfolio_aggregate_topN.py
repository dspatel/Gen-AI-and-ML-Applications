from __future__ import annotations

import os
from typing import Dict, Any, Optional, List, Tuple
import pandas as pd


# -------------------------
# Helpers
# -------------------------

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
    if "gross_pnl_per_share" not in trades.columns:
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
            "avg_r": None,
        }
    n = int(len(trades))
    wins = int((trades["r_multiple"] > 0).sum()) if "r_multiple" in trades.columns else 0
    total_r = float(trades["r_multiple"].sum()) if "r_multiple" in trades.columns else 0.0
    return {
        "trades": n,
        "total_r": total_r,
        "win_rate": float(wins / n) if n else None,
        "profit_factor": profit_factor(trades),
        "avg_r": float(total_r / n) if n else None,
    }


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


# -------------------------
# Core
# -------------------------

def main():
    root = os.getcwd()

    # Inputs
    ranked_path = os.path.join(root, "data", "research", "universe_ranked.csv")
    watchlist_path_candidates = [
        os.path.join(root, "data", "research", "universe_watchlist_top20.csv"),
        os.path.join(root, "data", "research", "universe_watchlist_top25.csv"),
        os.path.join(root, "data", "research", "universe_watchlist_top30.csv"),
    ]
    watchlist_path = next((p for p in watchlist_path_candidates if os.path.exists(p)), None)

    if watchlist_path is None:
        raise SystemExit(
            "Could not find universe_watchlist_topN.csv in data/research.\n"
            "Run Module S6 first or confirm file name."
        )

    conservative_base = os.path.join(root, "data", "research", "conservative")

    watch = load_csv(watchlist_path)
    if watch.empty:
        raise SystemExit(f"Missing/empty: {watchlist_path}")

    watch["symbol"] = watch["symbol"].astype(str).str.upper()
    symbols = watch["symbol"].tolist()

    # Load trades and (optional) decision-vs-reality per symbol
    symbol_rows: List[Dict[str, Any]] = []
    all_trades: List[pd.DataFrame] = []
    opp_rows: List[Dict[str, Any]] = []

    for sym in symbols:
        sym_dir = os.path.join(conservative_base, sym)
        trades_path = os.path.join(sym_dir, "trades.csv")
        dvr_path = os.path.join(sym_dir, "decision_vs_reality.csv")

        trades = load_csv(trades_path)
        if not trades.empty:
            trades["symbol"] = sym
            if "session_date" in trades.columns:
                trades["session_date"] = trades["session_date"].astype(str)
            all_trades.append(trades)

        s = summarize_trades(trades)
        symbol_rows.append({
            "symbol": sym,
            "final_rank_score": float(watch.loc[watch["symbol"] == sym, "final_rank_score"].iloc[0])
                if "final_rank_score" in watch.columns and (watch["symbol"] == sym).any() else None,
            "compatibility_score": float(watch.loc[watch["symbol"] == sym, "compatibility_score"].iloc[0])
                if "compatibility_score" in watch.columns and (watch["symbol"] == sym).any() else None,
            "opportunity_score": float(watch.loc[watch["symbol"] == sym, "opportunity_score"].iloc[0])
                if "opportunity_score" in watch.columns and (watch["symbol"] == sym).any() else None,
            **s
        })

        # Optional: opportunity coverage from decision-vs-reality file (if present)
        dvr = load_csv(dvr_path)
        if not dvr.empty and "future_mfe_r" in dvr.columns and "session_date" in dvr.columns:
            dvr["session_date"] = dvr["session_date"].astype(str)
            # ORB-defined 1R opportunities (same definition as earlier modules)
            dvr["is_1r_opp"] = dvr["future_mfe_r"] >= 1.0
            opp_days = int(dvr["is_1r_opp"].sum())

            traded_days = set(trades["session_date"]) if (not trades.empty and "session_date" in trades.columns) else set()
            dvr["traded"] = dvr["session_date"].isin(traded_days)
            traded_opp_days = int((dvr["is_1r_opp"] & dvr["traded"]).sum())

            recall = (traded_opp_days / opp_days) if opp_days else None

            opp_rows.append({
                "symbol": sym,
                "orb_defined_1r_opportunity_days": opp_days,
                "traded_1r_opportunity_days": traded_opp_days,
                "recall_on_1r_opportunities": recall
            })

    # Combine all trades
    combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()

    # Portfolio summary
    portfolio_summary = summarize_trades(combined_trades)
    portfolio_row = {
        "symbol": "TOPN_TOTAL",
        "final_rank_score": None,
        "compatibility_score": None,
        "opportunity_score": None,
        **portfolio_summary
    }

    symbol_breakdown = pd.DataFrame(symbol_rows)
    symbol_breakdown = pd.concat([symbol_breakdown, pd.DataFrame([portfolio_row])], ignore_index=True)

    # Trade calendar: how many trades per day (across all symbols)
    trade_calendar = pd.DataFrame()
    if not combined_trades.empty and "session_date" in combined_trades.columns:
        trade_calendar = (
            combined_trades.groupby("session_date")
            .size()
            .reset_index(name="trades_all_symbols")
            .sort_values("session_date")
        )

    # Opportunity coverage (optional)
    opp_cov = pd.DataFrame(opp_rows) if opp_rows else pd.DataFrame()

    # Output
    out_dir = os.path.join(root, "data", "research")
    ensure_dir(out_dir)

    out_summary = os.path.join(out_dir, "s7_topN_portfolio_summary.csv")
    out_calendar = os.path.join(out_dir, "s7_topN_trade_calendar.csv")
    out_breakdown = os.path.join(out_dir, "s7_topN_symbol_breakdown.csv")
    out_opp = os.path.join(out_dir, "s7_topN_opportunity_coverage.csv")

    symbol_breakdown.to_csv(out_summary, index=False)
    symbol_breakdown.to_csv(out_breakdown, index=False)  # same content, different filename for clarity
    if not trade_calendar.empty:
        trade_calendar.to_csv(out_calendar, index=False)
    if not opp_cov.empty:
        opp_cov.to_csv(out_opp, index=False)

    # Console output (quick insight)
    print("\n=== Module S7: Top-N Portfolio Aggregation ===")
    print(f"Watchlist: {os.path.basename(watchlist_path)} | N={len(symbols)}")
    print(f"Total trades (Top-N): {portfolio_summary['trades']}")
    print(f"Total R (Top-N): {portfolio_summary['total_r']:.4f}")
    print(f"Win rate (Top-N): {portfolio_summary['win_rate']}")
    print(f"Profit factor (Top-N): {portfolio_summary['profit_factor']}")
    print(f"Avg R/trade (Top-N): {portfolio_summary['avg_r']}")
    if not trade_calendar.empty:
        print(f"Active trading days: {trade_calendar.shape[0]}")
        print(f"Avg trades/day (on active days): {trade_calendar['trades_all_symbols'].mean():.3f}")

    print("\nSaved:")
    print(" -", out_summary)
    if not trade_calendar.empty:
        print(" -", out_calendar)
    if not opp_cov.empty:
        print(" -", out_opp)


if __name__ == "__main__":
    main()
