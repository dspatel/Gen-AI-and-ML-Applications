import os
import pandas as pd
import glob


def load_csv(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def compute_summary(trades: pd.DataFrame, label: str):
    if trades.empty:
        return {
            "label": label,
            "trades": 0,
            "total_r": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_r": 0.0
        }

    wins = trades[trades["r_multiple"] > 0]["r_multiple"].sum()
    losses = -trades[trades["r_multiple"] < 0]["r_multiple"].sum()

    return {
        "label": label,
        "trades": len(trades),
        "total_r": trades["r_multiple"].sum(),
        "win_rate": (trades["r_multiple"] > 0).mean(),
        "profit_factor": (wins / losses) if losses > 0 else float("inf"),
        "avg_r": trades["r_multiple"].mean()
    }


def main():
    root = os.getcwd()

    watchlist_path = os.path.join(
        root, "data", "research", "dynamic_selection", "daily_watchlist_top15.csv"
    )
    watchlist = load_csv(watchlist_path)
    if watchlist.empty:
        raise SystemExit("daily_watchlist_top15.csv is empty or missing")

    watchlist["symbol"] = watchlist["symbol"].str.upper()
    watchlist["session_date"] = watchlist["session_date"].astype(str)

    # Build lookup: date -> set(symbols)
    daily_symbols = (
        watchlist.groupby("session_date")["symbol"]
        .apply(set)
        .to_dict()
    )

    # Load all conservative trades
    trade_paths = glob.glob(
        os.path.join(root, "data", "research", "conservative", "*", "trades.csv")
    )

    trades_all = []
    for p in trade_paths:
        df = load_csv(p)
        if not df.empty:
            df["symbol"] = df["symbol"].str.upper()
            df["session_date"] = df["session_date"].astype(str)
            trades_all.append(df)

    trades_all = pd.concat(trades_all, ignore_index=True)

    # --- Fixed Top-20 baseline (already known, but recompute cleanly)
    fixed_summary = compute_summary(trades_all, "Fixed Top-20")

    # --- Dynamic Top-15 filter
    mask = trades_all.apply(
        lambda r: r["symbol"] in daily_symbols.get(r["session_date"], set()),
        axis=1
    )
    dynamic_trades = trades_all[mask].copy()

    dynamic_summary = compute_summary(dynamic_trades, "Dynamic Top-15")

    summary_df = pd.DataFrame([fixed_summary, dynamic_summary])
    print("\n=== DS3 Dynamic Selection Comparison ===")
    print(summary_df.to_string(index=False))

    out_path = os.path.join(
        root, "data", "research", "dynamic_selection", "dynamic_vs_fixed_summary.csv"
    )
    summary_df.to_csv(out_path, index=False)
    print("\nSaved:", out_path)


if __name__ == "__main__":
    main()
