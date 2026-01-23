from __future__ import annotations

import os
import glob
import pandas as pd


# ---- Gate rules (start here; we can tune later)
GATE = {
    "min_age": 2,
    "max_age": 10,
    "require_cross_dir_match": False,   # keep False for now
}


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def profit_factor_from_r(trades: pd.DataFrame) -> float:
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
        }

    total_r = float(trades["r_multiple"].sum())
    avg_r = float(trades["r_multiple"].mean())
    win_rate = float((trades["r_multiple"] > 0).mean())
    pf = profit_factor_from_r(trades)
    active_days = int(trades["session_date"].nunique())

    return {
        "label": label,
        "trades": int(len(trades)),
        "total_r": total_r,
        "win_rate": win_rate,
        "profit_factor_r": pf,
        "avg_r": avg_r,
        "active_days": active_days,
    }


def normalize_session_date(series: pd.Series) -> pd.Series:
    """
    Normalize anything that looks like a date/datetime into YYYY-MM-DD string.
    Works with strings, datetime, tz-aware timestamps, etc.
    """
    s = pd.to_datetime(series, errors="coerce")
    return s.dt.strftime("%Y-%m-%d")


def main():
    print("RUNNING FILE:", __file__)

    universe_path = os.path.join("data", "research", "universe_watchlist_top20.csv")
    regime_path = os.path.join("data", "research", "regime_age", "ema20_regime_age_top20_6mo.csv")

    uni = load_csv(universe_path)
    if uni.empty:
        raise SystemExit(f"Missing/empty universe: {universe_path}")
    if "symbol" not in uni.columns:
        raise SystemExit(f"Universe file must contain 'symbol' column: {universe_path}")

    symbols = set(uni["symbol"].astype(str).str.upper().tolist())

    regime = load_csv(regime_path)
    if regime.empty:
        raise SystemExit(f"Missing/empty regime file: {regime_path} (run R1 first)")
    if not {"symbol", "date", "regime_age_days"}.issubset(regime.columns):
        raise SystemExit(
            f"Regime file missing required columns. Found: {regime.columns.tolist()}"
        )

    regime["symbol"] = regime["symbol"].astype(str).str.upper()

    # ✅ Canonical join key: session_date (YYYY-MM-DD)
    regime["session_date"] = normalize_session_date(regime["date"])

    # Keep only what we need for join
    keep_cols = ["symbol", "session_date", "regime_age_days", "cross_direction", "bucket", "regime_side"]
    for c in keep_cols:
        if c not in regime.columns:
            # cross_direction/bucket/regime_side may exist; if missing, we can still run
            if c in ["cross_direction", "bucket", "regime_side"]:
                regime[c] = "UNKNOWN"
            else:
                raise SystemExit(f"Regime file missing column: {c}")

    regime = regime[keep_cols].copy()

    # Trades (from conservative engine)
    trade_paths = glob.glob(os.path.join("data", "research", "conservative", "*", "trades.csv"))
    all_trades = []
    for p in trade_paths:
        t = load_csv(p)
        if t.empty:
            continue
        if "symbol" not in t.columns or "session_date" not in t.columns or "r_multiple" not in t.columns:
            continue
        t["symbol"] = t["symbol"].astype(str).str.upper()
        if not t["symbol"].isin(symbols).any():
            continue

        # ✅ Canonicalize session_date
        t["session_date"] = normalize_session_date(t["session_date"])
        all_trades.append(t)

    if not all_trades:
        raise SystemExit("No usable trades found under data/research/conservative/*/trades.csv")

    trades = pd.concat(all_trades, ignore_index=True)
    trades = trades[trades["symbol"].isin(symbols)].copy()

    # Determine trade direction
    if "side" in trades.columns:
        trades["trade_dir"] = trades["side"].astype(str).str.upper().replace({"LONG": "UP", "SHORT": "DOWN"})
    elif "direction" in trades.columns:
        trades["trade_dir"] = trades["direction"].astype(str).str.upper().replace({"LONG": "UP", "SHORT": "DOWN"})
    else:
        # fallback inference: if exit > entry => UP else DOWN
        if "entry_price" in trades.columns and "exit_price" in trades.columns:
            trades["trade_dir"] = (trades["exit_price"] >= trades["entry_price"]).map({True: "UP", False: "DOWN"})
        else:
            trades["trade_dir"] = "UNKNOWN"

    # ✅ Merge regime age onto trades
    merged = trades.merge(regime, on=["symbol", "session_date"], how="left")

    missing_pct = float(merged["regime_age_days"].isna().mean())
    print(f"Regime coverage missing: {missing_pct:.2%} (should NOT be ~100%)")

    # Filter to rows where we have regime_age_days
    merged_valid = merged.dropna(subset=["regime_age_days"]).copy()
    merged_valid["regime_age_days"] = merged_valid["regime_age_days"].astype(int)

    # Apply gate
    gated = merged_valid[
        (merged_valid["regime_age_days"] >= GATE["min_age"]) &
        (merged_valid["regime_age_days"] <= GATE["max_age"])
    ].copy()

    if GATE["require_cross_dir_match"]:
        # only if cross_direction exists and trade_dir known
        gated = gated[gated["cross_direction"] == gated["trade_dir"]].copy()

    # Summaries
    out = pd.DataFrame([
        summarize(merged, "Fixed Top-20 (baseline)"),
        summarize(gated, f"Fixed Top-20 + Regime Gate age[{GATE['min_age']},{GATE['max_age']}] match={GATE['require_cross_dir_match']}")
    ])

    out_dir = os.path.join("data", "research", "regime_age")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "r2_fixed_top20_regime_gate_summary.csv")
    out.to_csv(out_path, index=False)

    print("\n=== R2 Regime Gate A/B (Fixed Top-20) ===")
    print("Gate:", GATE)
    print(out.to_string(index=False))
    print("\nSaved:", out_path)

    # Optional: save gated trades for inspection
    gated_path = os.path.join(out_dir, "r2_fixed_top20_gated_trades.csv")
    gated.to_csv(gated_path, index=False)
    print("Saved gated trades ->", gated_path)


if __name__ == "__main__":
    main()
