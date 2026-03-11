from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd


REQUIRED_OHLC = ("open", "high", "low", "close")


def _normalize_ohlc(df: pd.DataFrame, symbol: Optional[str] = None) -> pd.DataFrame:
    """Return a canonical OHLC dataframe with lowercase columns: open/high/low/close.

    Providers (notably yfinance) may return:
    - Titlecase columns (Open/High/Low/Close)
    - MultiIndex columns (e.g., when tickers are nested)
    - Tuple-like column keys

    This function must never rely on `.lower()` existing on the raw column object.
    """
    if df is None or getattr(df, "empty", True):
        return df

    out = df.copy()

    # 1) If MultiIndex columns are present (common with yfinance), first try to
    #    select the requested symbol cleanly, then extract the OHLC field level.
    if isinstance(out.columns, pd.MultiIndex):
        sym = symbol.upper() if symbol else None

        # Helper: find the best "field" level (the level that contains OHLC labels).
        def _field_level(cols: pd.MultiIndex) -> int:
            best_i, best_score = 0, -1
            for i in range(cols.nlevels):
                vals = {str(v).strip().lower() for v in cols.get_level_values(i).unique()}
                score = sum(1 for r in REQUIRED_OHLC if r in vals)
                if score > best_score:
                    best_i, best_score = i, score
            return best_i

        # Helper: find a ticker level (a level that contains the symbol).
        def _ticker_level(cols: pd.MultiIndex, sym_u: str) -> Optional[int]:
            for i in range(cols.nlevels):
                vals = {str(v).strip().upper() for v in cols.get_level_values(i).unique()}
                if sym_u in vals:
                    return i
            return None

        field_lvl = _field_level(out.columns)

        if sym:
            tkr_lvl = _ticker_level(out.columns, sym)
            if tkr_lvl is not None and tkr_lvl != field_lvl:
                # Choose the exact label as stored (preserve casing)
                uniq = list(out.columns.get_level_values(tkr_lvl).unique())
                match = next((u for u in uniq if str(u).strip().upper() == sym), None)
                if match is not None:
                    out = out.xs(match, axis=1, level=tkr_lvl, drop_level=True)
            else:
                # If we can't locate the symbol level but only one ticker exists, proceed.
                pass

        # After optional ticker selection, MultiIndex may remain (e.g., extra levels).
        if isinstance(out.columns, pd.MultiIndex):
            field_lvl = _field_level(out.columns)
            out.columns = [str(v) for v in out.columns.get_level_values(field_lvl)]

    # 2) Normalize column names robustly (works for Index and flattened columns).
    out.columns = [str(c).strip().lower() for c in out.columns]

    # 3) Validate required fields; allow extra columns (volume, adj close, etc.).
    missing = [c for c in REQUIRED_OHLC if c not in out.columns]
    if missing:
        raise ValueError(
            f"Missing required OHLC columns: {missing}. Columns={list(out.columns)}"
        )

    return out[list(REQUIRED_OHLC)].copy()

def fetch_intraday_yfinance(symbol: str, start_utc: datetime, end_utc: datetime, interval: str) -> pd.DataFrame:
    """Fetch intraday bars from yfinance in UTC window [start_utc, end_utc)."""
    import yfinance as yf
    # yfinance end is inclusive-ish; add a tiny buffer
    df = yf.download(
        tickers=symbol,
        start=start_utc,
        end=end_utc + timedelta(minutes=1),
        interval=interval,
        progress=False,
        auto_adjust=False,
        prepost=False,
        threads=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    # Ensure datetime index
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    # yfinance index is tz-aware UTC sometimes, sometimes naive; normalize to UTC tz-aware
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = _normalize_ohlc(df, symbol=symbol)
    return df.sort_index()
