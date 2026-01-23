# ============================================================
# Module F-M v3 (LOUD): Decision vs Reality Analyzer (Momentum v3 2-bar)
#
# Inputs:
#   data/SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v3.csv
#   data/SPY_trades_momentum_v3.csv
#
# Output:
#   data/SPY_decision_vs_reality_momentum_v3.csv
# ============================================================

from __future__ import annotations

print("✅ MODULE F-M v3 STARTED")

import os
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd


@dataclass(frozen=True)
class RealityConfig:
    timezone: str = "America/New_York"
    horizon_bars: int = 12
    ref_stop_buffer_perc: float = 0.02


def load_signals(path: str, tz: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Signals file not found: {path}")

    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError("Signals CSV must contain 'timestamp' column.")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["timestamp"] = df["timestamp"].dt.tz_convert(tz)
    df = df.set_index("timestamp").sort_index()

    if "entry_signal" in df.columns:
        df["entry_signal"] = df["entry_signal"].astype(bool)

    required = ["session_date", "open", "high", "low", "close", "orh", "orl", "orb_breakout_time", "orb_breakout"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Signals CSV missing required columns: {missing}")

    return df


def load_trades(path: str, tz: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    t = pd.read_csv(path)
    if t.empty:
        return t
    for col in ["signal_time", "entry_time", "exit_time"]:
        if col in t.columns:
            t[col] = pd.to_datetime(t[col], utc=True, errors="coerce").dt.tz_convert(tz)
    return t


def first_orb_breakout_direction(day_df: pd.DataFrame) -> Optional[str]:
    rows = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
    if rows.empty:
        return None
    return str(rows["orb_breakout"].iloc[0])


def get_orb_breakout_time(day_df: pd.DataFrame) -> Optional[pd.Timestamp]:
    b = day_df["orb_breakout_time"].dropna()
    if b.empty:
        return None
    bt = b.iloc[0]
    if isinstance(bt, pd.Timestamp):
        return bt
    return pd.to_datetime(bt, utc=True, errors="coerce").tz_convert(day_df.index.tz)


def get_trade_for_day(trades: pd.DataFrame, session_date: str) -> Optional[pd.Series]:
    if trades is None or trades.empty:
        return None
    d = trades[trades["session_date"] == session_date]
    if d.empty:
        return None
    return d.sort_values("entry_time").iloc[0]


def compute_future_mfe_mae_r(
    day_df: pd.DataFrame,
    start_ts: pd.Timestamp,
    side: str,
    entry_price: float,
    stop_price: float,
    horizon_bars: int
) -> Tuple[float, float, Optional[str]]:
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return 0.0, 0.0, None

    start_pos = day_df.index.get_indexer([start_ts])[0]
    end_pos = min(start_pos + horizon_bars, len(day_df) - 1)
    window = day_df.iloc[start_pos:end_pos + 1]

    mfe = 0.0
    mae = 0.0
    first_hit = None

    if side == "LONG":
        target_price = entry_price + risk
        stop_hit_price = entry_price - risk
    else:
        target_price = entry_price - risk
        stop_hit_price = entry_price + risk

    for _, row in window.iterrows():
        high = float(row["high"])
        low = float(row["low"])

        if side == "LONG":
            mfe = max(mfe, (high - entry_price) / risk)
            mae = min(mae, (low - entry_price) / risk)
            hit_target = high >= target_price
            hit_stop = low <= stop_hit_price
        else:
            mfe = max(mfe, (entry_price - low) / risk)
            mae = min(mae, (entry_price - high) / risk)
            hit_target = low <= target_price
            hit_stop = high >= stop_hit_price

        if first_hit is None:
            if hit_target and hit_stop:
                first_hit = "AMBIGUOUS"
            elif hit_target:
                first_hit = "PLUS_1R"
            elif hit_stop:
                first_hit = "MINUS_1R"

    return float(mfe), float(mae), first_hit


def build_reference_plan_for_skipped_day(day_df: pd.DataFrame, cfg: RealityConfig) -> Optional[Dict[str, Any]]:
    breakout_time = get_orb_breakout_time(day_df)
    if breakout_time is None:
        return None
    direction = first_orb_breakout_direction(day_df)
    if direction is None:
        return None

    post = day_df[day_df.index >= breakout_time]
    if post.empty:
        return None

    entry_ts = post.index[0]
    entry_price = float(post.iloc[0]["open"])

    orh = float(day_df["orh"].iloc[0])
    orl = float(day_df["orl"].iloc[0])

    buffer = entry_price * (cfg.ref_stop_buffer_perc / 100.0)

    if direction == "UP":
        side = "LONG"
        stop_price = orl - buffer
    else:
        side = "SHORT"
        stop_price = orh + buffer

    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return None

    return {
        "ref_side": side,
        "ref_entry_time": entry_ts,
        "ref_entry_price": entry_price,
        "ref_stop_price": stop_price,
        "ref_risk_per_share": risk,
        "ref_plan_type": "ORB_REFERENCE",
        "breakout_time": breakout_time,
        "thesis_direction": direction,
    }


def classify_outcome(took_trade: bool, first_hit: Optional[str]) -> str:
    if first_hit is None:
        return "NO_DECISIVE_MOVE"
    if took_trade:
        if first_hit == "PLUS_1R":
            return "CORRECT_TRADE"
        if first_hit == "MINUS_1R":
            return "WRONG_TRADE"
        return "AMBIGUOUS_TRADE"
    else:
        if first_hit == "PLUS_1R":
            return "MISSED_OPPORTUNITY"
        if first_hit == "MINUS_1R":
            return "CORRECT_SKIP"
        return "AMBIGUOUS_SKIP"


def main():
    cfg = RealityConfig()

    signals_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb_signals_momentum_v3.csv")
    trades_path = os.path.join(os.getcwd(), "data", "SPY_trades_momentum_v3.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_decision_vs_reality_momentum_v3.csv")

    print("\n[Module F-M v3] Loading signals + trades...")
    print(f"Signals path: {signals_path}")
    print(f"Trades path : {trades_path}")

    if not os.path.exists(signals_path):
        raise FileNotFoundError(f"Missing signals file: {signals_path}")

    signals = load_signals(signals_path, cfg.timezone)
    trades = load_trades(trades_path, cfg.timezone)

    sessions = sorted(signals["session_date"].unique())
    print(f"Signals rows: {len(signals):,} | sessions: {len(sessions)}")
    print(f"Trades rows : {len(trades):,}" if not trades.empty else "Trades rows : 0")

    rows: List[Dict[str, Any]] = []

    for session_date in sessions:
        day_df = signals[signals["session_date"] == session_date].copy()
        if day_df.empty:
            continue

        breakout_time = get_orb_breakout_time(day_df)
        direction = first_orb_breakout_direction(day_df)
        if breakout_time is None or direction is None:
            continue

        trade = get_trade_for_day(trades, session_date)
        took_trade = trade is not None

        if took_trade:
            side = str(trade["side"]).upper()
            entry_time = trade["entry_time"]
            entry_price = float(trade["entry_price"])
            stop_price = float(trade["stop_price"])
            risk = float(trade["risk_per_share"])
            plan_type = "ACTUAL_TRADE"
        else:
            ref = build_reference_plan_for_skipped_day(day_df, cfg)
            if ref is None:
                continue
            side = ref["ref_side"]
            entry_time = ref["ref_entry_time"]
            entry_price = ref["ref_entry_price"]
            stop_price = ref["ref_stop_price"]
            risk = ref["ref_risk_per_share"]
            plan_type = ref["ref_plan_type"]

        mfe_r, mae_r, first_hit = compute_future_mfe_mae_r(
            day_df=day_df,
            start_ts=entry_time,
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            horizon_bars=cfg.horizon_bars
        )

        outcome = classify_outcome(took_trade, first_hit)

        rows.append({
            "session_date": session_date,
            "thesis_direction": direction,
            "took_trade": took_trade,
            "plan_type": plan_type,
            "breakout_time": breakout_time,
            "entry_time_ref": entry_time,
            "side_ref": side,
            "entry_price_ref": entry_price,
            "stop_price_ref": stop_price,
            "risk_per_share_ref": risk,
            "future_horizon_bars": cfg.horizon_bars,
            "future_mfe_r": mfe_r,
            "future_mae_r": mae_r,
            "first_hit": first_hit,
            "outcome_class": outcome,
        })

    result = pd.DataFrame(rows).sort_values("session_date")
    result.to_csv(out_path, index=False)

    print(f"\n✅ Saved decision vs reality (momentum v3) to: {out_path}")
    print(f"Rows evaluated (ORB-confirmed sessions): {len(result)}")

    if result.empty:
        print("\n⚠️ No ORB-confirmed days were evaluated (either no breakouts in data or breakout columns empty).")
        return

    total = len(result)
    took = int(result["took_trade"].sum())
    skipped = total - took
    counts = result["outcome_class"].value_counts(dropna=False).to_dict()
    directional_opportunity_rate = float((result["first_hit"] == "PLUS_1R").mean())

    print("\n--- Summary (Momentum v3 2-Bar) ---")
    print(f"ORB-confirmed days evaluated: {total}")
    print(f"Days traded: {took} | Days skipped: {skipped}")
    print("Outcome class counts:", counts)
    print(f"Directional opportunity rate (hit +1R within horizon): {directional_opportunity_rate:.2%}")


if __name__ == "__main__":
    main()
