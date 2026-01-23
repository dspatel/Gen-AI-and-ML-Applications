# ============================================================
# Module E-M v3: Trade Execution (Momentum v3 2-bar confirmation)
#
# Input:
#   data/SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v3.csv
#
# Outputs:
#   data/SPY_trades_momentum_v3.csv
#   data/SPY_daily_summary_momentum_v3.csv
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import pandas as pd


@dataclass(frozen=True)
class ExecConfig:
    timezone: str = "America/New_York"
    eod_exit_time: str = "15:55"
    ema_exit_confirm_closes: int = 1
    allow_one_trade_per_day: bool = True


def load_signals_csv(path: str, tz: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Signals CSV not found: {path}")

    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError("CSV must contain 'timestamp' column.")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["timestamp"] = df["timestamp"].dt.tz_convert(tz)
    df = df.set_index("timestamp").sort_index()

    required = [
        "session_date",
        "open", "high", "low", "close",
        "ema20",
        "entry_signal", "side",
        "planned_stop_price", "risk_per_share",
        "signal_reason",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns for execution: {missing}")

    df["entry_signal"] = df["entry_signal"].astype(bool)
    df["planned_stop_price"] = pd.to_numeric(df["planned_stop_price"], errors="coerce")
    df["risk_per_share"] = pd.to_numeric(df["risk_per_share"], errors="coerce")

    return df


def eod_timestamp(session_date: str, cfg: ExecConfig) -> pd.Timestamp:
    return pd.Timestamp(f"{session_date} {cfg.eod_exit_time}", tz=cfg.timezone)


def simulate_trade_for_signal(
    day_df: pd.DataFrame,
    signal_ts: pd.Timestamp,
    cfg: ExecConfig,
) -> Optional[Dict[str, Any]]:
    sig = day_df.loc[signal_ts]
    side = str(sig["side"]).upper()
    if side not in {"LONG", "SHORT"}:
        return None

    idx_pos = day_df.index.get_indexer([signal_ts])[0]
    if idx_pos < 0 or idx_pos + 1 >= len(day_df):
        return None

    entry_ts = day_df.index[idx_pos + 1]
    entry_price = float(day_df.loc[entry_ts]["open"])

    stop_price = float(sig["planned_stop_price"])
    risk = float(sig["risk_per_share"])
    if risk <= 0 or pd.isna(risk) or pd.isna(stop_price):
        return None

    mfe = 0.0
    mae = 0.0
    ema_confirm = 0

    exit_ts = None
    exit_price = None
    exit_reason = None

    eod_ts = eod_timestamp(str(sig["session_date"]), cfg)
    if eod_ts not in day_df.index:
        eod_ts = day_df.index[day_df.index <= eod_ts].max()

    for ts, row in day_df.loc[entry_ts:].iterrows():
        if side == "LONG":
            favorable = float(row["high"]) - entry_price
            adverse = float(row["low"]) - entry_price
            mfe = max(mfe, favorable)
            mae = min(mae, adverse)

            if float(row["low"]) <= stop_price:
                exit_ts = ts
                exit_price = stop_price
                exit_reason = "STOP_HIT"
                break

            if float(row["close"]) < float(row["ema20"]):
                ema_confirm += 1
            else:
                ema_confirm = 0

        else:
            favorable = entry_price - float(row["low"])
            adverse = entry_price - float(row["high"])
            mfe = max(mfe, favorable)
            mae = min(mae, adverse)

            if float(row["high"]) >= stop_price:
                exit_ts = ts
                exit_price = stop_price
                exit_reason = "STOP_HIT"
                break

            if float(row["close"]) > float(row["ema20"]):
                ema_confirm += 1
            else:
                ema_confirm = 0

        if ema_confirm >= cfg.ema_exit_confirm_closes:
            pos = day_df.index.get_indexer([ts])[0]
            if pos + 1 < len(day_df):
                exit_ts = day_df.index[pos + 1]
                exit_price = float(day_df.loc[exit_ts]["open"])
            else:
                exit_ts = ts
                exit_price = float(row["close"])
            exit_reason = "EMA_EXIT"
            break

        if ts == eod_ts:
            exit_ts = ts
            exit_price = float(row["close"])
            exit_reason = "EOD_EXIT"
            break

    if exit_ts is None:
        last_ts = day_df.index[-1]
        exit_ts = last_ts
        exit_price = float(day_df.loc[last_ts]["close"])
        exit_reason = "FORCED_EXIT"

    if side == "LONG":
        pnl = exit_price - entry_price
        r_mult = pnl / risk
    else:
        pnl = entry_price - exit_price
        r_mult = pnl / risk

    bars_held = int(day_df.index.get_indexer([exit_ts])[0] - day_df.index.get_indexer([entry_ts])[0]) + 1

    return {
        "session_date": str(sig["session_date"]),
        "symbol": "SPY",
        "side": side,
        "setup_type": "MOMENTUM_V3_2BAR",
        "signal_time": signal_ts,
        "entry_time": entry_ts,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "risk_per_share": risk,
        "exit_time": exit_ts,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "gross_pnl_per_share": pnl,
        "r_multiple": r_mult,
        "mfe_per_share": mfe,
        "mae_per_share": mae,
        "mfe_r": (mfe / risk) if risk else None,
        "mae_r": (mae / risk) if risk else None,
        "bars_held": bars_held,
        "signal_reason": str(sig.get("signal_reason", "")),
        "signal_type": str(sig.get("signal_type", "")),
    }


def build_daily_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=[
            "session_date", "symbol", "trades", "wins", "losses", "win_rate",
            "total_r", "avg_r", "stop_hit_rate", "ema_exit_rate", "eod_exit_rate",
            "avg_mfe_r", "avg_mae_r"
        ])

    rows = []
    for (d, sym), grp in trades.groupby(["session_date", "symbol"]):
        n = len(grp)
        wins = int((grp["r_multiple"] > 0).sum())
        losses = int((grp["r_multiple"] <= 0).sum())
        rows.append({
            "session_date": d,
            "symbol": sym,
            "trades": n,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / n if n else 0.0,
            "total_r": float(grp["r_multiple"].sum()),
            "avg_r": float(grp["r_multiple"].mean()),
            "stop_hit_rate": float((grp["exit_reason"] == "STOP_HIT").mean()),
            "ema_exit_rate": float((grp["exit_reason"] == "EMA_EXIT").mean()),
            "eod_exit_rate": float((grp["exit_reason"] == "EOD_EXIT").mean()),
            "avg_mfe_r": float(grp["mfe_r"].mean()),
            "avg_mae_r": float(grp["mae_r"].mean()),
        })
    return pd.DataFrame(rows).sort_values(["session_date", "symbol"])


def main():
    cfg = ExecConfig()

    in_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v3.csv")
    trades_path = os.path.join(os.getcwd(), "data", "SPY_trades_momentum_v3.csv")
    daily_path = os.path.join(os.getcwd(), "data", "SPY_daily_summary_momentum_v3.csv")

    print("\n[Module E-M v3] Loading momentum v3 signals CSV:")
    print(f"  {in_path}")
    df = load_signals_csv(in_path, cfg.timezone)
    sessions = sorted(df["session_date"].unique())
    print(f"Loaded rows: {len(df):,} | sessions: {len(sessions)}")

    all_trades: List[Dict[str, Any]] = []

    for session_date in sessions:
        day_df = df[df["session_date"] == session_date].copy()
        sigs = day_df[day_df["entry_signal"]].copy()
        if sigs.empty:
            continue

        sig_ts_list = sorted(list(sigs.index))
        if cfg.allow_one_trade_per_day:
            sig_ts_list = sig_ts_list[:1]

        for sig_ts in sig_ts_list:
            trade = simulate_trade_for_signal(day_df, sig_ts, cfg)
            if trade:
                all_trades.append(trade)

    trades = pd.DataFrame(all_trades)

    print(f"\n✅ Trades generated (momentum v3 2-bar): {len(trades)}")
    if not trades.empty:
        show_cols = [
            "session_date", "side",
            "signal_time", "entry_time", "entry_price",
            "stop_price", "exit_time", "exit_price",
            "exit_reason", "r_multiple", "mfe_r", "mae_r"
        ]
        print("\nPreview (first 10 trades):")
        print(trades[show_cols].head(10).round(4))

    trades.to_csv(trades_path, index=False)
    print(f"\nSaved trades to: {trades_path}")

    daily = build_daily_summary(trades)
    daily.to_csv(daily_path, index=False)
    print(f"Saved daily summary to: {daily_path}")

    print("\nNext: run Module F-M v3 and $10k sim v3.\n")


if __name__ == "__main__":
    main()
