from __future__ import annotations

import os
import pandas as pd


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def compute_ema(series: pd.Series, period: int = 20) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def bucket_age(age: int) -> str:
    if age <= 1:
        return "CROSS_0_1"
    if 2 <= age <= 5:
        return "CROSS_2_5"
    if 6 <= age <= 20:
        return "CROSS_6_20"
    return "CROSS_21P"


def main():
    in_path = os.path.join("data", "research", "daily_yahoo", "daily_top20_6mo.csv")
    if not os.path.exists(in_path):
        raise SystemExit(f"Missing daily file: {in_path} (run R0 first)")

    df = pd.read_csv(in_path)
    required = {"date", "symbol", "close"}
    if not required.issubset(df.columns):
        raise SystemExit(f"Daily file missing required columns {required}. Found: {df.columns.tolist()}")

    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    out_rows = []

    for sym, g in df.groupby("symbol", sort=True):
        g = g.sort_values("date").copy()

        g["ema20"] = compute_ema(g["close"], 20)
        g["regime_side"] = (g["close"] >= g["ema20"]).map({True: "ABOVE", False: "BELOW"})

        # cross occurs when regime_side changes vs prior day
        g["cross"] = g["regime_side"] != g["regime_side"].shift(1)
        g["cross"] = g["cross"].fillna(False)

        # last_cross_date: forward-fill the date when cross=True
        g["last_cross_date"] = pd.NaT
        g.loc[g["cross"], "last_cross_date"] = g.loc[g["cross"], "date"]
        g["last_cross_date"] = g["last_cross_date"].ffill()

        # regime_age_days: number of sessions since last cross (0 on the cross day)
        # Compute by counting rows since last_cross_date within the symbol group
        # We do it by tracking the last cross index.
        last_cross_idx = None
        ages = []
        cross_dir = []

        prev_side = None
        for i, row in g.iterrows():
            side = row["regime_side"]
            is_cross = bool(row["cross"])

            if prev_side is None:
                prev_side = side

            if is_cross:
                # cross direction based on side change
                if prev_side == "BELOW" and side == "ABOVE":
                    cd = "UP"
                elif prev_side == "ABOVE" and side == "BELOW":
                    cd = "DOWN"
                else:
                    cd = "UNKNOWN"
                last_cross_idx = i
            else:
                cd = None

            age = 0 if last_cross_idx is None else (i - last_cross_idx)
            ages.append(int(age))

            # carry forward most recent cross direction
            if cd is None:
                cross_dir.append(cross_dir[-1] if cross_dir else "UNKNOWN")
            else:
                cross_dir.append(cd)

            prev_side = side

        g["regime_age_days"] = ages
        g["cross_direction"] = cross_dir
        g["bucket"] = g["regime_age_days"].apply(bucket_age)

        out_rows.append(g[[
            "date", "symbol", "close", "ema20",
            "regime_side", "cross", "cross_direction",
            "last_cross_date", "regime_age_days", "bucket"
        ]])

    out = pd.concat(out_rows, ignore_index=True)
    out["date"] = out["date"].dt.date.astype(str)
    if "last_cross_date" in out.columns:
        out["last_cross_date"] = pd.to_datetime(out["last_cross_date"]).dt.date.astype(str)

    out_dir = os.path.join("data", "research", "regime_age")
    ensure_dir(out_dir)
    out_path = os.path.join(out_dir, "ema20_regime_age_top20_6mo.csv")
    out.to_csv(out_path, index=False)

    print("\n✅ Saved regime-age file ->", out_path)
    print("Sample:")
    print(out.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
