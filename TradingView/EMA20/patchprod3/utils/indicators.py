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


def compute_ema20_cross_stats(
    df: pd.DataFrame,
    asof_date: str,
    lookback_td: int,
    include_event_day: bool = True,
    ema_col: str = "EMA20",
) -> dict:
    """Compute EMA20 range-cross stats over the last `lookback_td` trading days ending at `asof_date`.

    Cross definition (consistent with project):
      A day is *cross-capable* if (Low <= EMA20 <= High).

    Bull/Bear direction uses *prior day's close side* as the anchor:
      prev_side = sign(prev_close - prev_ema)
      - Bull cross when prev_side < 0 and today's High >= today's EMA (touch/above)
      - Bear cross when prev_side > 0 and today's Low <= today's EMA (touch/below)
      - If prev_side == 0, we treat today's Close >= EMA as Bull else Bear (rare)

    Returns:
      {
        'lookback_td': int,
        'count_total': int,
        'count_bull': int,
        'count_bear': int,
        'days_since_last_cross': int|None,
        'cross_density': float|None
      }
    """
    out = {
        "lookback_td": int(lookback_td),
        "count_total": 0,
        "count_bull": 0,
        "count_bear": 0,
        "days_since_last_cross": None,
        "cross_density": None,
    }
    if df is None or df.empty or lookback_td <= 0:
        return out

    d = df.copy()
    # Normalize date column
    if "Date" not in d.columns and "date" in d.columns:
        d["Date"] = d["date"]
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce").dt.date
    d = d.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    asof = pd.to_datetime(asof_date, errors="coerce").date()
    if asof is None:
        return out

    # Cut to as-of date (optionally exclude event day)
    if include_event_day:
        d = d[d["Date"] <= asof]
    else:
        d = d[d["Date"] < asof]

    if len(d) < 2:
        return out

    # We need one extra prior day to determine direction for the first day in window
    window = d.tail(lookback_td + 1).reset_index(drop=True)
    if len(window) < 2:
        return out

    bull = 0
    bear = 0
    total = 0

    for i in range(1, len(window)):
        prev = window.iloc[i - 1]
        cur = window.iloc[i]
        try:
            prev_close = float(prev["Close"])
            prev_ema = float(prev[ema_col])
            cur_low = float(cur["Low"])
            cur_high = float(cur["High"])
            cur_close = float(cur["Close"])
            cur_ema = float(cur[ema_col])
        except Exception:
            continue

        touched = (cur_low <= cur_ema) and (cur_high >= cur_ema)
        if not touched:
            continue

        prev_side = 0
        if prev_close > prev_ema:
            prev_side = 1
        elif prev_close < prev_ema:
            prev_side = -1

        if prev_side < 0 and cur_high >= cur_ema:
            bull += 1
            total += 1
        elif prev_side > 0 and cur_low <= cur_ema:
            bear += 1
            total += 1
        else:
            # Edge case: prev close ~= prev ema. Use close side on current day.
            if cur_close >= cur_ema:
                bull += 1
            else:
                bear += 1
            total += 1

    out["count_total"] = int(total)
    out["count_bull"] = int(bull)
    out["count_bear"] = int(bear)
    out["cross_density"] = (float(total) / float(lookback_td)) if lookback_td > 0 else None

    # Days since last cross (trading days distance within available history)
    if total > 0:
        # Find last crossed day index within the trimmed dataset d
        # Recompute crosses over full d for correct distance
        crosses_dates = []
        for i in range(1, len(d)):
            prev = d.iloc[i - 1]
            cur = d.iloc[i]
            try:
                prev_close = float(prev["Close"])
                prev_ema = float(prev[ema_col])
                cur_low = float(cur["Low"])
                cur_high = float(cur["High"])
                cur_ema = float(cur[ema_col])
            except Exception:
                continue
            touched = (cur_low <= cur_ema) and (cur_high >= cur_ema)
            if not touched:
                continue
            prev_side = 1 if prev_close > prev_ema else (-1 if prev_close < prev_ema else 0)
            if prev_side < 0 and cur_high >= cur_ema:
                crosses_dates.append(cur["Date"])
            elif prev_side > 0 and cur_low <= cur_ema:
                crosses_dates.append(cur["Date"])
            else:
                crosses_dates.append(cur["Date"])
        if crosses_dates:
            last_cross_date = crosses_dates[-1]
            # trading day distance = number of rows from last_cross_date to end-1
            try:
                idx_last = d.index[d["Date"] == last_cross_date].tolist()
                if idx_last:
                    out["days_since_last_cross"] = int((len(d) - 1) - idx_last[-1])
            except Exception:
                pass

    return out
