from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def read_bars(path: str) -> pd.DataFrame:
    df = load_csv(path)
    if df.empty:
        return df
    if "timestamp" not in df.columns:
        # tolerate unnamed timestamp column
        if df.columns[0].lower().startswith("unnamed"):
            df = df.rename(columns={df.columns[0]: "timestamp"})
        else:
            raise ValueError(f"Cannot find timestamp column in {path}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    df = df.sort_values("timestamp").set_index("timestamp")
    return df


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_ema_slope_perc(ema: pd.Series, lookback: int = 5) -> pd.Series:
    shifted = ema.shift(lookback)
    return (ema - shifted) / shifted.replace(0, pd.NA) * 100.0


def price_vs_ema(close: float, ema: float, eps: float = 1e-9) -> str:
    if abs(close - ema) <= eps:
        return "AT"
    return "ABOVE" if close > ema else "BELOW"


def ema_cross_count(pvs: pd.Series) -> int:
    s = pvs.replace({"AT": pd.NA}).ffill()
    s = s[s.notna()]
    if len(s) < 2:
        return 0
    flips = (s != s.shift(1)).sum()
    return int(max(flips - 1, 0))


@dataclass(frozen=True)
class Stage2Config:
    cutoff_time: str = "10:30"    # ET session time
    ema_period: int = 20
    slope_lookback: int = 5
    chop_lookback: int = 10
    max_chop_crosses: int = 2
    max_abs_dist_from_ema_perc: float = 0.60

    top_n_daily: int = 15  # dynamic top N per day


def ts_for_time(session_date: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"{session_date} {hhmm}")


def score_symbol_day(day_df: pd.DataFrame, cfg: Stage2Config) -> Optional[Dict[str, Any]]:
    """
    Scores early-session quality up to cutoff.
    Returns dict with stage2_score and diagnostics.
    """
    session_date = str(day_df["session_date"].iloc[0])
    cutoff = pd.Timestamp(f"{session_date} {cfg.cutoff_time}")

    # Align cutoff timezone to match bars index
    idx = day_df.index
    if getattr(idx, "tz", None) is not None and cutoff.tzinfo is None:
        # localize naive cutoff into the same tz as index
        cutoff = cutoff.tz_localize(idx.tz)

    early = day_df[day_df.index <= cutoff].copy()









    
    if early.empty or len(early) < max(cfg.slope_lookback + 2, cfg.chop_lookback):
        return None

    # Ensure EMA columns exist
    if "ema20" not in early.columns:
        early["ema20"] = compute_ema(early["close"], cfg.ema_period)

    early["ema_slope_perc"] = compute_ema_slope_perc(early["ema20"], lookback=cfg.slope_lookback).fillna(0.0)
    early["dist_from_ema_perc"] = (early["close"] - early["ema20"]) / early["ema20"].replace(0, pd.NA) * 100.0
    early["pvs"] = [price_vs_ema(c, e) for c, e in zip(early["close"], early["ema20"])]

    # Chop (use last chop_lookback bars up to cutoff)
    look = early.tail(cfg.chop_lookback)
    crosses = ema_cross_count(look["pvs"])

    # Directional slope at cutoff (absolute strength matters)
    slope_now = float(early["ema_slope_perc"].iloc[-1])
    abs_slope = abs(slope_now)

    # Avoid chasing: distance at cutoff
    dist_now = float(early["dist_from_ema_perc"].iloc[-1])
    abs_dist = abs(dist_now)

    # Guardrails
    chop_ok = crosses <= cfg.max_chop_crosses
    dist_ok = abs_dist <= cfg.max_abs_dist_from_ema_perc

    if not chop_ok or not dist_ok:
        stage2_score = 0.0
    else:
        # Simple interpretable score:
        # - reward abs slope
        # - penalize distance from EMA
        # - penalize chop
        stage2_score = (abs_slope * 50.0) + (max(0.0, (cfg.max_abs_dist_from_ema_perc - abs_dist)) * 30.0) + (max(0, (cfg.max_chop_crosses - crosses)) * 10.0)

    direction_bias = "UP" if slope_now > 0 else ("DOWN" if slope_now < 0 else "NEUTRAL")

    return {
        "session_date": session_date,
        "stage2_score": float(stage2_score),
        "direction_bias": direction_bias,
        "ema_slope_perc_at_cutoff": slope_now,
        "abs_dist_from_ema_perc_at_cutoff": abs_dist,
        "ema_crosses_recent": int(crosses),
        "chop_ok": bool(chop_ok),
        "dist_ok": bool(dist_ok),
    }


def main():
    cfg = Stage2Config()
    root = os.getcwd()

    eligible_path = os.path.join(root, "data", "research", "dynamic_selection", "eligible_pool.csv")
    if not os.path.exists(eligible_path):
        raise SystemExit("Missing eligible_pool.csv. Run DS1 first.")

    eligible = load_csv(eligible_path)
    if eligible.empty:
        raise SystemExit("eligible_pool.csv is empty.")

    eligible["symbol"] = eligible["symbol"].astype(str).str.upper()
    symbols = eligible["symbol"].tolist()

    # Bars location (we reuse the standardized bars with session_date that you already have)
    # Expect: data/research/conservative/<SYMBOL>/bars_with_orb_ema.csv
    conservative_base = os.path.join(root, "data", "research", "conservative")

    out_dir = os.path.join(root, "data", "research", "dynamic_selection")
    ensure_dir(out_dir)

    all_rows: List[Dict[str, Any]] = []

    for sym in symbols:
        bars_path = os.path.join(conservative_base, sym, "bars_with_orb_ema.csv")
        bars = read_bars(bars_path)
        if bars.empty:
            continue

        bars["symbol"] = sym
        if "session_date" not in bars.columns:
            # derive session_date from timestamp date
            bars["session_date"] = bars.index.date.astype(str)

        for sd, day_df in bars.groupby("session_date", sort=True):
            scored = score_symbol_day(day_df, cfg)
            if not scored:
                continue

            row = {
                "symbol": sym,
                "stage1_score": float(eligible.loc[eligible["symbol"] == sym, "stage1_score"].iloc[0]) if "stage1_score" in eligible.columns else None,
                **scored
            }
            all_rows.append(row)


    out_all = pd.DataFrame(all_rows)
    # --- FIX: enforce one row per (session_date, symbol)
    out_all = out_all.sort_values(
    ["session_date", "symbol", "stage2_score"],
    ascending=[True, True, False]
    )

    out_all = out_all.drop_duplicates(
    subset=["session_date", "symbol"],
    keep="first"
    )





    
    out_all_path = os.path.join(out_dir, "daily_watchlist_all.csv")
    out_all.to_csv(out_all_path, index=False)
    
    # Produce per-day Top-N list
    daily_topn_rows = []
    for sd, g in out_all.groupby("session_date", sort=True):
        gg = g.sort_values("stage2_score", ascending=False).head(cfg.top_n_daily).copy()
        gg["rank_in_day"] = range(1, len(gg) + 1)
        daily_topn_rows.append(gg)

    daily_topn = pd.concat(daily_topn_rows, ignore_index=True) if daily_topn_rows else pd.DataFrame()
    daily_topn_path = os.path.join(out_dir, f"daily_watchlist_top{cfg.top_n_daily}.csv")
    daily_topn.to_csv(daily_topn_path, index=False)

    print("\n=== DS2 Stage-2 Daily Top-N ===")
    print(f"Eligible pool size: {len(symbols)} | Daily Top-N: {cfg.top_n_daily} | Cutoff: {cfg.cutoff_time}")
    print("Saved:")
    print(" -", out_all_path)
    print(" -", daily_topn_path)
    if not daily_topn.empty:
        print("\nSample (first 20 rows of daily top-N):")
        print(daily_topn.head(20)[["session_date","symbol","rank_in_day","stage2_score","direction_bias","ema_slope_perc_at_cutoff","ema_crosses_recent","abs_dist_from_ema_perc_at_cutoff"]].to_string(index=False))


if __name__ == "__main__":
    main()
