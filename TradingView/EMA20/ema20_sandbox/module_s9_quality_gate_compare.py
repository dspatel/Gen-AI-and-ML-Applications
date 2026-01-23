import os
import glob
import pandas as pd


# -----------------------
# CONFIG (tune here)
# -----------------------
GATE = {
    # slope is in "percent" terms in your DS2 file
    "min_abs_ema_slope_perc": 0.20,       # try 0.20, 0.25, 0.30
    "max_ema_crosses_recent": 1,          # 0 or 1 is strict, 2 is looser
    "max_abs_dist_from_ema_perc": 0.45,   # try 0.35–0.60
}

DYNAMIC_TOP_N_FILE = "daily_watchlist_top15.csv"   # in data/research/dynamic_selection
STAGE2_ALL_FILE = "daily_watchlist_all.csv"        # in data/research/dynamic_selection
FIXED_TOP_FILE = "universe_watchlist_top20.csv"    # in data/research


# -----------------------
# Helpers
# -----------------------
def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def profit_factor_from_r(trades: pd.DataFrame) -> float:
    # PF computed in R-space (consistent and robust)
    wins = trades.loc[trades["r_multiple"] > 0, "r_multiple"].sum()
    losses = -trades.loc[trades["r_multiple"] < 0, "r_multiple"].sum()
    if losses <= 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def summarize(trades: pd.DataFrame, label: str) -> dict:
    if trades.empty:
        return {
            "label": label,
            "trades": 0,
            "total_r": 0.0,
            "win_rate": None,
            "profit_factor_r": None,
            "avg_r": None,
            "active_days": 0,
            "avg_trades_per_active_day": None,
        }

    total_r = float(trades["r_multiple"].sum())
    avg_r = float(trades["r_multiple"].mean())
    win_rate = float((trades["r_multiple"] > 0).mean())
    pf = profit_factor_from_r(trades)

    active_days = trades["session_date"].nunique()
    avg_trades_day = float(len(trades) / active_days) if active_days else None

    return {
        "label": label,
        "trades": int(len(trades)),
        "total_r": total_r,
        "win_rate": win_rate,
        "profit_factor_r": pf,
        "avg_r": avg_r,
        "active_days": int(active_days),
        "avg_trades_per_active_day": avg_trades_day,
    }


def main():
    root = os.getcwd()

    # Paths
    dyn_dir = os.path.join(root, "data", "research", "dynamic_selection")
    dynamic_top_path = os.path.join(dyn_dir, DYNAMIC_TOP_N_FILE)
    stage2_all_path = os.path.join(dyn_dir, STAGE2_ALL_FILE)
    fixed_top_path = os.path.join(root, "data", "research", FIXED_TOP_FILE)

    # Load inputs
    dyn_top = load_csv(dynamic_top_path)
    if dyn_top.empty:
        raise SystemExit(f"Missing/empty: {dynamic_top_path}")

    stage2_all = load_csv(stage2_all_path)
    if stage2_all.empty:
        raise SystemExit(
            f"Missing/empty: {stage2_all_path}\n"
            f"Re-run DS2 to generate daily_watchlist_all.csv"
        )

    fixed_top = load_csv(fixed_top_path)
    if fixed_top.empty:
        raise SystemExit(f"Missing/empty: {fixed_top_path}")

    # Normalize keys
    for df in (dyn_top, stage2_all, fixed_top):
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.upper()

    dyn_top["session_date"] = dyn_top["session_date"].astype(str)
    stage2_all["session_date"] = stage2_all["session_date"].astype(str)
    fixed_symbols = set(fixed_top["symbol"].astype(str).str.upper().tolist())

    # Build lookup for Stage-2 metrics by (date, symbol)
    key_cols = ["session_date", "symbol"]
    needed_cols = [
        "ema_slope_perc_at_cutoff",
        "ema_crosses_recent",
        "abs_dist_from_ema_perc_at_cutoff",
    ]
    for c in needed_cols:
        if c not in stage2_all.columns:
            raise SystemExit(f"Stage-2 file missing column: {c}")

    # Ensure single row per (date,symbol)
    stage2_all = stage2_all.sort_values(key_cols + ["stage2_score"], ascending=[True, True, False])
    stage2_all = stage2_all.drop_duplicates(subset=key_cols, keep="first")

    stage2_lookup = stage2_all.set_index(key_cols)[needed_cols]

    # Gate function (ex-ante)
    def passes_gate(sd: str, sym: str) -> bool:
        try:
            row = stage2_lookup.loc[(sd, sym)]
        except KeyError:
            return False  # if we can't score it by cutoff, don't trade it
        slope = float(row["ema_slope_perc_at_cutoff"])
        crosses = int(row["ema_crosses_recent"])
        dist = float(row["abs_dist_from_ema_perc_at_cutoff"])
        return (
            abs(slope) >= GATE["min_abs_ema_slope_perc"]
            and crosses <= GATE["max_ema_crosses_recent"]
            and dist <= GATE["max_abs_dist_from_ema_perc"]
        )

    # Dynamic membership lookup: date -> allowed symbols (top N)
    dyn_top = dyn_top.sort_values(["session_date", "rank_in_day"])
    dyn_map = dyn_top.groupby("session_date")["symbol"].apply(set).to_dict()

    # Load ALL conservative trades from disk
    trade_paths = glob.glob(os.path.join(root, "data", "research", "conservative", "*", "trades.csv"))
    all_trades = []
    for p in trade_paths:
        t = load_csv(p)
        if t.empty:
            continue
        t["symbol"] = t["symbol"].astype(str).str.upper()
        t["session_date"] = t["session_date"].astype(str)
        all_trades.append(t)

    if not all_trades:
        raise SystemExit("No trades.csv found under data/research/conservative/*/trades.csv")

    trades = pd.concat(all_trades, ignore_index=True)

    # Baselines
    fixed_trades = trades[trades["symbol"].isin(fixed_symbols)].copy()

    dynamic_trades = trades[
        trades.apply(lambda r: r["symbol"] in dyn_map.get(r["session_date"], set()), axis=1)
    ].copy()

    # Apply gate
    fixed_gated = fixed_trades[
        fixed_trades.apply(lambda r: passes_gate(r["session_date"], r["symbol"]), axis=1)
    ].copy()

    dynamic_gated = dynamic_trades[
        dynamic_trades.apply(lambda r: passes_gate(r["session_date"], r["symbol"]), axis=1)
    ].copy()

    # Summaries
    results = [
        summarize(fixed_trades, "Fixed Top-20 (no gate)"),
        summarize(dynamic_trades, "Dynamic Top-15 (no gate)"),
        summarize(fixed_gated, f"Fixed Top-20 + S9 Gate"),
        summarize(dynamic_gated, f"Dynamic Top-15 + S9 Gate"),
    ]
    out = pd.DataFrame(results)

    # Save
    out_path = os.path.join(dyn_dir, "s9_quality_gate_comparison.csv")
    out.to_csv(out_path, index=False)

    # Print
    print("\n=== Module S9: Quality Gate Comparison ===")
    print("Gate thresholds:", GATE)
    print(out.to_string(index=False))
    print("\nSaved:", out_path)


if __name__ == "__main__":
    main()
