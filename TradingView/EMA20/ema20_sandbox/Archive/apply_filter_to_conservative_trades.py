# ============================================================
# Module H2: Apply Momentum Filter to Conservative Trades
# ============================================================

from __future__ import annotations
import os
import pandas as pd

def main():
    trades_path = os.path.join(os.getcwd(), "data", "SPY_trades_conservative.csv")
    filt_path = os.path.join(os.getcwd(), "data", "SPY_filter_decisions_v1.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_trades_conservative_filtered_v1.csv")

    trades = pd.read_csv(trades_path)
    filt = pd.read_csv(filt_path)

    merged = trades.merge(
        filt[["session_date", "filter_allow", "filter_reason"]],
        on="session_date",
        how="left"
    )

    filtered = merged[merged["filter_allow"] == True].copy()
    filtered.to_csv(out_path, index=False)

    print("\n=== Module H2: Conservative Trades Filtered ===")
    print(f"Original trades: {len(trades)}")
    print(f"After filter   : {len(filtered)}")
    print(f"Saved to {out_path}")

if __name__ == "__main__":
    main()
