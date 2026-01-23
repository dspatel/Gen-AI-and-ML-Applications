from __future__ import annotations

import re
import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

from .config import Config

# yfinance native intraday minute intervals
YF_NATIVE_MINUTES = {1, 2, 5, 15, 30, 60, 90}

def valid_test_date(s: str) -> bool:
    """Validate YYYY-MM-DD."""
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.strip()))

def interval_str(minutes: int) -> str:
    """Convert minutes to yfinance interval string."""
    return f"{minutes}m" if minutes < 60 else "60m"

def fetch_bars(symbol: str, cfg: Config) -> pd.DataFrame:
    """
    Fetch OHLCV bars from Yahoo via yfinance.

    Returns columns:
      time_local, open, high, low, close, volume

    Raises:
      ValueError if cfg.candle_minutes is not a Yahoo/yfinance native interval.
    """
    if cfg.candle_minutes not in YF_NATIVE_MINUTES:
        raise ValueError(
            f"Unsupported candle_minutes={cfg.candle_minutes}. "
            f"Use one of: {sorted(YF_NATIVE_MINUTES)} (Yahoo/yfinance native intervals)."
        )

    df = yf.download(
        tickers=symbol,
        interval=interval_str(cfg.candle_minutes),
        period=cfg.period,
        auto_adjust=False,
        prepost=cfg.prepost,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    # MultiIndex handling (sometimes yfinance returns (OHLCV, Ticker))
    if isinstance(df.columns, pd.MultiIndex):
        if symbol in df.columns.get_level_values(-1):
            df = df.xs(symbol, axis=1, level=-1, drop_level=True)
        elif symbol in df.columns.get_level_values(0):
            df = df.xs(symbol, axis=1, level=0, drop_level=True)

    df.reset_index(inplace=True)

    if "Datetime" in df.columns:
        df.rename(columns={"Datetime": "time"}, inplace=True)
    elif "Date" in df.columns:
        df.rename(columns={"Date": "time"}, inplace=True)

    df.rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"},
        inplace=True,
    )

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{symbol}: missing columns after normalization: {missing} | cols={list(df.columns)}")

    t = pd.to_datetime(df["time"])
    if t.dt.tz is None:
        t = t.dt.tz_localize("UTC")

    df["time_local"] = t.dt.tz_convert(ZoneInfo(cfg.tz))
    df = df.sort_values("time_local").reset_index(drop=True)

    return df[["time_local", "open", "high", "low", "close", "volume"]]
