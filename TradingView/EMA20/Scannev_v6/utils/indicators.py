import pandas as pd

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def add_ema20_columns(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Expects columns: Open, High, Low, Close, Volume (standard OHLCV).
    Adds:
      EMA20 (based on Close)
      EMA20_H (based on High)
      EMA20_L (based on Low)
    """
    df = df.copy()
    df["EMA20"] = ema(df["Close"], period)
    df["EMA20_H"] = ema(df["High"], period)
    df["EMA20_L"] = ema(df["Low"], period)
    return df

def find_latest_range_cross(df: pd.DataFrame, ema_col: str, lookback_days: int) -> dict | None:
    """
    "Crossover can be considered as price crossing EMA20 even if close is not on the other side."
    We implement that as: (Low <= EMA <= High) on that day.

    Direction: UP if Close >= EMA else DOWN (simple, consistent rule).
    Returns dict with cross_date, direction, ema_value.
    """
    if len(df) < lookback_days + 1:
        lookback = df
    else:
        lookback = df.tail(lookback_days)

    crossed = (lookback["Low"] <= lookback[ema_col]) & (lookback["High"] >= lookback[ema_col])
    if not crossed.any():
        return None

    last_idx = crossed[crossed].index[-1]
    row = df.loc[last_idx]

    direction = "UP" if row["Close"] >= row[ema_col] else "DOWN"
    return {
        "cross_date": pd.to_datetime(row["Date"]).date().isoformat() if "Date" in df.columns else str(last_idx),
        "direction": direction,
        "ema_value": float(row[ema_col]),
    }

def compute_window_high_low_excluding_today(df: pd.DataFrame, window_days: int) -> tuple[float, float] | None:
    """
    Window is last `window_days` trading days excluding today (last row).
    Returns (window_high, window_low) or None if insufficient history.
    """
    if len(df) < window_days + 1:
        return None

    window = df.iloc[-(window_days+1):-1]  # exclude last row (today)
    return float(window["High"].max()), float(window["Low"].min())
