# ============================================================
# Module H1: Momentum Filter (v1) for Conservative ORB Trades
# ============================================================

from __future__ import annotations
import os
import pandas as pd

SLOPE_THRESHOLD = 0.03
MAX_DIST_PERC = 1.00

def load_features(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.sort_values("timestamp")
    return df

def get_orb_breakout(day_df: pd.DataFrame):
    rows = day_df[day_df["orb_breakout"].isin(["UP", "DOWN"])]
    if rows.empty:
        return None, None
    r = rows.iloc[0]
    return r["orb_breakout"], pd.to_datetime(r["orb_breakout_time"])

def evaluate_filter(day_df: pd.DataFrame, direction: str) -> dict:
    post = day_df[day_df["orb_locked"] == True]
    if post.empty:
        return {"allow": False, "reason": "NO_POST_ORB_BARS"}

    r = post.iloc[0]
    slope = float(r["ema_slope_perc"])
    dist = abs(float(r["dist_from_ema_perc"]))
    pve = str(r["price_vs_ema"])

    reasons = []
    if direction == "UP":
        if slope < SLOPE_THRESHOLD: reasons.append("SLOPE_FAIL")
        if pve != "ABOVE": reasons.append("PVE_FAIL")
    else:
        if slope > -SLOPE_THRESHOLD: reasons.append("SLOPE_FAIL")
        if pve != "BELOW": reasons.append("PVE_FAIL")
    if dist > MAX_DIST_PERC: reasons.append("EXTENSION_FAIL")

    allow = len(reasons) == 0
    return {
        "allow": allow,
        "ema_slope_perc": slope,
        "dist_from_ema_perc": dist,
        "price_vs_ema": pve,
        "reason": "PASS" if allow else "|".join(reasons)
    }

def main():
    feat_path = os.path.join(os.getcwd(), "data", "SPY_30d_5m_yahoo_ema20_orb.csv")
    out_path = os.path.join(os.getcwd(), "data", "SPY_filter_decisions_v1.csv")

    df = load_features(feat_path)
    rows = []
    for session_date, day in df.groupby("session_date", sort=True):
        direction, _ = get_orb_breakout(day)
        if direction is None: continue
        res = evaluate_filter(day, direction)
        rows.append({
            "session_date": session_date,
            "orb_direction": direction,
            "filter_allow": res["allow"],
            "filter_reason": res["reason"],
            "ema_slope_perc": res.get("ema_slope_perc"),
            "dist_from_ema_perc": res.get("dist_from_ema_perc"),
            "price_vs_ema": res.get("price_vs_ema"),
        })

    out = pd.DataFrame(rows).sort_values("session_date")
    out.to_csv(out_path, index=False)

    print("\n=== Module H1: Momentum Filter v1 ===")
    print(f"Days evaluated: {len(out)}")
    print(f"Allowed days : {int(out['filter_allow'].sum())}")
    print(f"Blocked days : {int((~out['filter_allow']).sum())}")
    print(f"Saved to {out_path}")

if __name__ == "__main__":
    main()
