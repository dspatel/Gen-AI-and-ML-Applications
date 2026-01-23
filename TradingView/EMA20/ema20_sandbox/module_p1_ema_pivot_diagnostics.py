# ============================================================
# Module P1: EMA-Pivot Diagnostics (read-only)
#
# Purpose:
#   Quantify EMA-20 "respect" and pivot-quality after ORB direction.
#   This does NOT change strategy logic; it just creates diagnostics.
#
# Inputs (per symbol):
#   data/research/conservative/<SYMBOL>/bars_with_orb_ema.csv
#     - produced by S4 step "orb"
#     - expected columns: timestamp, session_date, close, high, low, ema20,
#       price_vs_ema, orb_breakout, orb_breakout_time
#
# Outputs (per symbol):
#   data/research/conservative/<SYMBOL>/ema_pivot_diagnostics.csv
#
# Outputs (universe):
#   data/research/conservative/ema_pivot_profile_universe.csv
#
# Run:
#   python module_p1_ema_pivot_diagnostics.py
# ============================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class P1Config:
    # Pivot definition: local extrema using left/right bars
    pivot_left: int = 2
    pivot_right: int = 2

    # Post-breakout analysis window (bars)
    post_breakout_window_bars: int = 60  # e.g., 5 hours on 5m bars

    # EMA "hold" definition
    # Long day: EMA is respected if close >= ema20
    # Short day: EMA is respected if close <= ema20

    # Simple "compression" proxy: how often range is small vs typical
    compression_lookback: int = 20
    compression_quantile: float = 0.35  # lower TR quantile = "compressed"


def _load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    return df


def _ensure_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.dropna(subset=["timestamp"]).copy()
        df = df.sort_values("timestamp").set_index("timestamp")
    else:
        # If S4 saved with index=True, timestamp may already be index in the CSV
        # But pandas would name it something else; try common pattern
        if df.columns[0].lower().startswith("unnamed"):
            df.rename(columns={df.columns[0]: "timestamp"}, inplace=True)
            df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
            df = df.dropna(subset=["timestamp"]).copy()
            df = df.sort_values("timestamp").set_index("timestamp")
    return df


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def _detect_pivots(df: pd.DataFrame, cfg: P1Config) -> Tuple[pd.Series, pd.Series]:
    """
    Returns (pivot_high_bool, pivot_low_bool) using left/right window extrema.
    """
    L, R = cfg.pivot_left, cfg.pivot_right
    if len(df) < (L + R + 1):
        return pd.Series(False, index=df.index), pd.Series(False, index=df.index)

    highs = df["high"]
    lows = df["low"]

    pivot_high = pd.Series(False, index=df.index)
    pivot_low = pd.Series(False, index=df.index)

    # A bar is pivot high if its high is strictly greater than highs in L bars before and R bars after
    for i in range(L, len(df) - R):
        h = highs.iat[i]
        left = highs.iloc[i - L:i]
        right = highs.iloc[i + 1:i + 1 + R]
        if (h > left.max()) and (h > right.max()):
            pivot_high.iat[i] = True

        l = lows.iat[i]
        left_l = lows.iloc[i - L:i]
        right_l = lows.iloc[i + 1:i + 1 + R]
        if (l < left_l.min()) and (l < right_l.min()):
            pivot_low.iat[i] = True

    return pivot_high, pivot_low


def _post_breakout_slice(day_df: pd.DataFrame, cfg: P1Config) -> Optional[pd.DataFrame]:
    bt = day_df["orb_breakout_time"].dropna()
    bd = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
    if bt.empty or bd.empty:
        return None

    breakout_time = pd.to_datetime(bt.iloc[0], errors="coerce")
    if pd.isna(breakout_time):
        return None

    post = day_df[day_df.index >= breakout_time].copy()
    if post.empty:
        return None

    post = post.iloc[: cfg.post_breakout_window_bars].copy()
    return post


def _ema_respect_metrics(post: pd.DataFrame, direction: str) -> Dict[str, Any]:
    """
    Long: respect if close >= ema20
    Short: respect if close <= ema20
    """
    close = post["close"].astype(float)
    ema = post["ema20"].astype(float)

    if direction == "UP":
        respected = close >= ema
        violations = close < ema
        dist = (close - ema) / ema.replace(0, pd.NA) * 100.0  # positive is above
        pullback_depth = dist.min()  # most negative (deepest under EMA)
    else:
        respected = close <= ema
        violations = close > ema
        dist = (ema - close) / ema.replace(0, pd.NA) * 100.0  # positive is below (good for short)
        pullback_depth = dist.min()  # most negative (deepest above EMA in short framing)

    respect_ratio = float(respected.mean()) if len(respected) else None
    violation_count = int(violations.sum()) if len(violations) else 0

    # Long: consecutive holds above EMA (max run)
    # Short: consecutive holds below EMA (max run)
    max_hold = 0
    current = 0
    for ok in respected.fillna(False).tolist():
        if ok:
            current += 1
            max_hold = max(max_hold, current)
        else:
            current = 0

    return {
        "ema_respect_ratio": respect_ratio,
        "ema_violation_count": violation_count,
        "ema_max_consecutive_hold_bars": max_hold,
        "ema_pullback_depth_perc": float(pullback_depth) if pd.notna(pullback_depth) else None,
    }


def _pivot_quality_metrics(post: pd.DataFrame, direction: str, cfg: P1Config) -> Dict[str, Any]:
    piv_h, piv_l = _detect_pivots(post, cfg)

    ema = post["ema20"].astype(float)

    # For long: relevant pivots are pivot highs
    # For short: relevant pivots are pivot lows
    if direction == "UP":
        piv = post[piv_h].copy()
        piv["pivot_price"] = piv["high"].astype(float)
        piv["pivot_height_perc"] = (piv["pivot_price"] - ema.loc[piv.index]) / ema.loc[piv.index].replace(0, pd.NA) * 100.0
        pivot_count = int(len(piv))
        avg_pivot_height = float(piv["pivot_height_perc"].mean()) if pivot_count else None
        max_pivot_height = float(piv["pivot_height_perc"].max()) if pivot_count else None
    else:
        piv = post[piv_l].copy()
        piv["pivot_price"] = piv["low"].astype(float)
        # For shorts, a stronger pivot is further BELOW EMA (i.e., EMA - pivot)
        piv["pivot_height_perc"] = (ema.loc[piv.index] - piv["pivot_price"]) / ema.loc[piv.index].replace(0, pd.NA) * 100.0
        pivot_count = int(len(piv))
        avg_pivot_height = float(piv["pivot_height_perc"].mean()) if pivot_count else None
        max_pivot_height = float(piv["pivot_height_perc"].max()) if pivot_count else None

    # Compression proxy: fraction of bars with TR below rolling quantile threshold
    tr = _true_range(post).fillna(0.0)
    roll_q = tr.rolling(cfg.compression_lookback, min_periods=5).quantile(cfg.compression_quantile)
    compressed = tr <= roll_q
    compression_ratio = float(compressed.mean()) if len(compressed) else None

    return {
        "pivot_count": pivot_count,
        "avg_pivot_height_perc": avg_pivot_height,
        "max_pivot_height_perc": max_pivot_height,
        "compression_ratio": compression_ratio,
    }


def analyze_symbol(symbol: str, sym_dir: str, cfg: P1Config) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    bars_path = os.path.join(sym_dir, "bars_with_orb_ema.csv")
    df = _load_csv(bars_path)
    if df.empty:
        return pd.DataFrame(), {"symbol": symbol, "ok": False, "reason": "MISSING_OR_EMPTY_BARS"}

    df = _ensure_dt_index(df)
    needed = {"session_date", "close", "high", "low", "ema20", "orb_breakout", "orb_breakout_time"}
    missing = needed - set(df.columns)
    if missing:
        return pd.DataFrame(), {"symbol": symbol, "ok": False, "reason": f"MISSING_COLS:{sorted(missing)}"}

    rows: List[Dict[str, Any]] = []

    for sd, day_df in df.groupby("session_date", sort=True):
        post = _post_breakout_slice(day_df, cfg)
        if post is None or post.empty:
            continue

        direction = str(post["orb_breakout"].dropna().iloc[0])  # UP or DOWN
        if direction not in {"UP", "DOWN"}:
            continue

        ema_metrics = _ema_respect_metrics(post, direction)
        piv_metrics = _pivot_quality_metrics(post, direction, cfg)

        bt = pd.to_datetime(post["orb_breakout_time"].dropna().iloc[0], errors="coerce")
        rows.append({
            "symbol": symbol,
            "session_date": sd,
            "direction": direction,
            "breakout_time": bt,
            "bars_analyzed": int(len(post)),
            **ema_metrics,
            **piv_metrics,
        })

    diag = pd.DataFrame(rows).sort_values(["session_date"])
    # Aggregate profile per symbol
    profile = {
        "symbol": symbol,
        "ok": True,
        "sessions_with_breakout": int(diag["session_date"].nunique()) if not diag.empty else 0,
        "avg_ema_respect_ratio": float(diag["ema_respect_ratio"].mean()) if not diag.empty else None,
        "avg_ema_violation_count": float(diag["ema_violation_count"].mean()) if not diag.empty else None,
        "avg_ema_max_consecutive_hold_bars": float(diag["ema_max_consecutive_hold_bars"].mean()) if not diag.empty else None,
        "avg_ema_pullback_depth_perc": float(diag["ema_pullback_depth_perc"].mean()) if not diag.empty else None,
        "avg_pivot_count": float(diag["pivot_count"].mean()) if not diag.empty else None,
        "avg_pivot_height_perc": float(diag["avg_pivot_height_perc"].mean()) if not diag.empty else None,
        "avg_max_pivot_height_perc": float(diag["max_pivot_height_perc"].mean()) if not diag.empty else None,
        "avg_compression_ratio": float(diag["compression_ratio"].mean()) if not diag.empty else None,
    }
    return diag, profile


def main():
    cfg = P1Config()

    base_dir = os.path.join(os.getcwd(), "data", "research", "conservative")
    if not os.path.exists(base_dir):
        raise SystemExit(f"Base dir not found: {base_dir}")

    universe_profiles: List[Dict[str, Any]] = []

    for symbol in sorted(os.listdir(base_dir)):
        sym_dir = os.path.join(base_dir, symbol)
        if not os.path.isdir(sym_dir):
            continue

        diag, profile = analyze_symbol(symbol, sym_dir, cfg)
        if not profile.get("ok"):
            # Skip silently or log
            print(f"❌ {symbol}: {profile.get('reason')}")
            continue

        out_path = os.path.join(sym_dir, "ema_pivot_diagnostics.csv")
        diag.to_csv(out_path, index=False)
        universe_profiles.append(profile)

        print(f"✅ {symbol}: EMA-pivot diagnostics saved -> {out_path} | sessions={profile['sessions_with_breakout']}")

    universe_df = pd.DataFrame(universe_profiles)
    universe_out = os.path.join(base_dir, "ema_pivot_profile_universe.csv")
    universe_df.to_csv(universe_out, index=False)

    print("\n✅ Universe EMA-pivot profile saved ->", universe_out)

    if not universe_df.empty:
        print("\nTop 10 by avg_ema_respect_ratio:")
        print(universe_df.sort_values("avg_ema_respect_ratio", ascending=False).head(10)[
            ["symbol", "sessions_with_breakout", "avg_ema_respect_ratio", "avg_ema_violation_count", "avg_pivot_height_perc"]
        ])

        print("\nTop 10 by avg_pivot_height_perc:")
        print(universe_df.sort_values("avg_pivot_height_perc", ascending=False).head(10)[
            ["symbol", "sessions_with_breakout", "avg_pivot_height_perc", "avg_ema_respect_ratio", "avg_ema_violation_count"]
        ])


if __name__ == "__main__":
    main()
