
from __future__ import annotations

from datetime import datetime
import pandas as pd


def compute_daily_or(session_df: pd.DataFrame, or_start: datetime, or_end: datetime) -> dict:
    """Compute Opening Range (OR) over [or_start, or_end)."""
    if session_df is None or session_df.empty:
        return {}

    window = session_df.loc[(session_df.index >= or_start) & (session_df.index < or_end)]
    if window.empty:
        return {}

    or_high = float(window["High"].max())
    or_low = float(window["Low"].min())
    width = float(or_high - or_low)

    return {
        "or_high": or_high,
        "or_low": or_low,
        "or_width": width,
        "or_start": or_start,
        "or_end": or_end,
    }
