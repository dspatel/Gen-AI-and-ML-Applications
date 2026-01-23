# ============================================================
# Module H3: $10k Equity Simulation (Conservative vs Conservative+Filter v1)
#
# This is a standalone script so you DON'T have to modify any existing files.
#
# Inputs:
#   data/SPY_trades_conservative.csv
#   data/SPY_trades_conservative_filtered_v1.csv
#
# Outputs:
#   data/SPY_equity_curve_conservative.csv
#   data/SPY_equity_curve_conservative_filtered_v1.csv
#   data/SPY_equity_summary_conservative_vs_filtered_v1.csv
#
# Run:
#   python test_equity_simulation_10k_conservative_filtered_v1.py
# ============================================================

from __future__ import annotations

import os
import math
from dataclasses import dataclass
from typing import Optional, Dict, Any

import pandas as pd


@dataclass(frozen=True)
class EquityConfig:
    start_capital: float = 10_000.0
    risk_pct: float = 0.01
    allow_compounding: bool = True
    max_leverage_shares: Optional[int] = None
    slippage_per_share: float = 0.0
    commission_per_trade: float = 0.0


def load_trades(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return df

    # Parse optional timestamps for sorting (not required)
    for col in ["entry_time", "exit_time", "signal_time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    required = ["session_date", "gross_pnl_per_share", "risk_per_share"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{os.path.basename(path)} missing required columns: {missing}")

    df["gross_pnl_per_share"] = pd.to_numeric(df["gross_pnl_per_share"], errors="coerce")
    df["risk_per_share"] = pd.to_numeric(df["risk_per_share"], errors="coerce")

    # Prefer chronological ordering
    if "entry_time" in df.columns and df["entry_time"].notna().any():
        df = df.sort_values(["entry_time"])
    else:
        df = df.sort_values(["session_date"])

    return df.reset_index(drop=True)


def simulate_equity(trades: pd.DataFrame, cfg: EquityConfig, label: str) -> Dict[str, Any]:
    if trades.empty:
        return {
            "label": label,
            "trades": 0,
            "final_equity": cfg.start_capital,
            "total_return_pct": 0.0,
            "max_drawdown_dollars": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate": None,
            "profit_factor": None,
            "avg_trade_pnl": None,
            "equity_curve": pd.DataFrame(),
        }

    equity = cfg.start_capital
    peak = equity
    max_dd = 0.0

    curve_rows = []
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    pnl_list = []

    for i, row in trades.iterrows():
        risk_per_share = float(row["risk_per_share"])
        pnl_per_share = float(row["gross_pnl_per_share"])

        if not (risk_per_share > 0) or pd.isna(risk_per_share) or pd.isna(pnl_per_share):
            continue

        base_equity = equity if cfg.allow_compounding else cfg.start_capital
        risk_dollars = base_equity * cfg.risk_pct
        shares = math.floor(risk_dollars / risk_per_share)
        shares = max(shares, 1)

        if cfg.max_leverage_shares is not None:
            shares = min(shares, cfg.max_leverage_shares)

        trade_slippage = cfg.slippage_per_share * shares
        trade_commission = cfg.commission_per_trade
        trade_pnl = (pnl_per_share * shares) - trade_slippage - trade_commission

        equity += trade_pnl
        pnl_list.append(trade_pnl)

        if trade_pnl > 0:
            wins += 1
            gross_profit += trade_pnl
        else:
            losses += 1
            gross_loss += abs(trade_pnl)

        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

        curve_rows.append({
            "trade_index": i,
            "session_date": row.get("session_date"),
            "side": row.get("side"),
            "setup_type": row.get("setup_type"),
            "entry_time": row.get("entry_time"),
            "exit_time": row.get("exit_time"),
            "shares": shares,
            "risk_per_share": risk_per_share,
            "pnl_per_share": pnl_per_share,
            "trade_pnl": trade_pnl,
            "equity": equity,
            "peak_equity": peak,
            "drawdown": dd,
            "drawdown_pct": (dd / peak) if peak else 0.0,
            "r_multiple": row.get("r_multiple"),
            "exit_reason": row.get("exit_reason"),
        })

    curve = pd.DataFrame(curve_rows)
    final_equity = float(equity)
    total_return_pct = (final_equity / cfg.start_capital - 1.0) * 100.0
    max_drawdown_pct = (max_dd / curve["peak_equity"].max()) * 100.0 if not curve.empty else 0.0
    win_rate = wins / (wins + losses) if (wins + losses) else None
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    avg_trade_pnl = (sum(pnl_list) / len(pnl_list)) if pnl_list else None

    return {
        "label": label,
        "trades": int(len(curve)),
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "max_drawdown_dollars": float(max_dd),
        "max_drawdown_pct": float(max_drawdown_pct),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_trade_pnl": avg_trade_pnl,
        "equity_curve": curve,
    }


def fmt(x, nd=2):
    return "NA" if x is None or (isinstance(x, float) and pd.isna(x)) else round(float(x), nd)


def main():
    cfg = EquityConfig()

    base = os.path.join(os.getcwd(), "data")
    cons_path = os.path.join(base, "SPY_trades_conservative.csv")
    filt_path = os.path.join(base, "SPY_trades_conservative_filtered_v1.csv")

    cons_trades = load_trades(cons_path)
    filt_trades = load_trades(filt_path)

    cons = simulate_equity(cons_trades, cfg, "conservative")
    filt = simulate_equity(filt_trades, cfg, "conservative_filtered_v1")

    cons_curve_path = os.path.join(base, "SPY_equity_curve_conservative.csv")
    filt_curve_path = os.path.join(base, "SPY_equity_curve_conservative_filtered_v1.csv")

    cons["equity_curve"].to_csv(cons_curve_path, index=False)
    filt["equity_curve"].to_csv(filt_curve_path, index=False)

    summary_path = os.path.join(base, "SPY_equity_summary_conservative_vs_filtered_v1.csv")
    summary = pd.DataFrame([{
        "mode": cons["label"],
        "trades": cons["trades"],
        "final_equity": cons["final_equity"],
        "total_return_pct": cons["total_return_pct"],
        "max_drawdown_dollars": cons["max_drawdown_dollars"],
        "max_drawdown_pct": cons["max_drawdown_pct"],
        "win_rate": cons["win_rate"],
        "profit_factor": cons["profit_factor"],
        "avg_trade_pnl": cons["avg_trade_pnl"],
        "risk_pct": cfg.risk_pct,
        "start_capital": cfg.start_capital,
        "compounding": cfg.allow_compounding,
    }, {
        "mode": filt["label"],
        "trades": filt["trades"],
        "final_equity": filt["final_equity"],
        "total_return_pct": filt["total_return_pct"],
        "max_drawdown_dollars": filt["max_drawdown_dollars"],
        "max_drawdown_pct": filt["max_drawdown_pct"],
        "win_rate": filt["win_rate"],
        "profit_factor": filt["profit_factor"],
        "avg_trade_pnl": filt["avg_trade_pnl"],
        "risk_pct": cfg.risk_pct,
        "start_capital": cfg.start_capital,
        "compounding": cfg.allow_compounding,
    }])
    summary.to_csv(summary_path, index=False)

    print("\n=== $10,000 Equity Simulation (Conservative vs Conservative+Filter v1) ===")
    print(f"Start capital: ${cfg.start_capital:,.2f} | Risk per trade: {cfg.risk_pct*100:.2f}% | Compounding: {cfg.allow_compounding}")

    for res in [cons, filt]:
        print(f"\n--- {res['label'].upper()} ---")
        print(f"Trades: {res['trades']}")
        print(f"Final equity: ${res['final_equity']:,.2f}")
        print(f"Total return: {res['total_return_pct']:.2f}%")
        print(f"Max drawdown: ${res['max_drawdown_dollars']:,.2f} ({res['max_drawdown_pct']:.2f}%)")
        print(f"Win rate: {fmt(res['win_rate']*100 if res['win_rate'] is not None else None, 2)}%")
        print(f"Profit factor: {fmt(res['profit_factor'], 3)}")
        print(f"Avg trade PnL: ${fmt(res['avg_trade_pnl'], 2)}")

    print("\nSaved:")
    print(f"  {cons_curve_path}")
    print(f"  {filt_curve_path}")
    print(f"  {summary_path}\n")


if __name__ == "__main__":
    main()
