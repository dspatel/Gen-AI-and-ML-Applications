from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd
import yfinance as yf

CST = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")

SUPPORTED_YF_INTERVALS = {"1m","2m","5m","15m","30m","60m","90m","1h","1d"}

def _interval_to_timedelta(interval: str) -> timedelta:
    if interval.endswith("m"):
        return timedelta(minutes=int(interval[:-1]))
    if interval.endswith("h"):
        return timedelta(hours=int(interval[:-1]))
    if interval == "1d":
        return timedelta(days=1)
    raise ValueError(f"Unsupported interval for timedelta conversion: {interval}")

def _flatten_col(c: Any) -> str:
    if isinstance(c, tuple):
        parts = [str(x) for x in c if x is not None and str(x).strip() and str(x).lower() != "nan"]
        return "_".join(parts) if parts else "col"
    return str(c)

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ["_".join([str(x) for x in tup if str(x) != ""]) for tup in df.columns.values]
    df.columns = [_flatten_col(c).lower().replace(" ", "_").replace(".", "_") for c in df.columns]
    if "adj_close" in df.columns:
        df = df.drop(columns=["adj_close"])
    return df

def fetch_yf_intraday(symbol: str, interval: str, start_cst: datetime, end_cst: datetime) -> pd.DataFrame:
    if interval not in SUPPORTED_YF_INTERVALS:
        raise ValueError(f"Interval '{interval}' not supported. Supported: {sorted(SUPPORTED_YF_INTERVALS)}")

    start_utc = start_cst.astimezone(UTC).replace(tzinfo=None)
    end_utc = (end_cst.astimezone(UTC) + timedelta(minutes=1)).replace(tzinfo=None)

    df = yf.download(
        tickers=symbol,
        interval=interval,
        start=start_utc,
        end=end_utc,
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=False,
        group_by="column",
    )

    if df is None or df.empty:
        return pd.DataFrame()

    df = _normalize_columns(df)

    # Make index tz-aware UTC
    if getattr(df.index, "tz", None) is None:
        df.index = df.index.tz_localize(UTC)
    else:
        df.index = df.index.tz_convert(UTC)

    sym = symbol.lower()

    def pick(base: str) -> str:
        if base in df.columns:
            return base
        cand1 = f"{sym}_{base}"
        if cand1 in df.columns:
            return cand1
        cand2 = f"{base}_{sym}"
        if cand2 in df.columns:
            return cand2
        raise KeyError(f"Missing column '{base}' for {symbol}. Columns sample={list(df.columns)[:15]}")

    o = pick("open"); h = pick("high"); l = pick("low"); c = pick("close")

    v = None
    for vb in ("volume", f"{sym}_volume", f"volume_{sym}"):
        if vb in df.columns:
            v = vb
            break

    out = pd.DataFrame(index=df.index)
    out["open"] = df[o]
    out["high"] = df[h]
    out["low"] = df[l]
    out["close"] = df[c]
    out["volume"] = df[v] if v else pd.NA
    return out

def filter_to_regular_session_cst(df_utc: pd.DataFrame, session_dates_cst: list[str], session_start: str, session_end: str) -> pd.DataFrame:
    if df_utc.empty:
        return df_utc

    open_ts_cst = df_utc.index.tz_convert(CST)
    cst_date = pd.Series(open_ts_cst.strftime("%Y-%m-%d"), index=df_utc.index)

    sh, sm = [int(x) for x in session_start.split(":")]
    eh, em = [int(x) for x in session_end.split(":")]
    open_min = sh * 60 + sm
    close_min = eh * 60 + em

    mins = pd.Series(open_ts_cst.hour * 60 + open_ts_cst.minute, index=df_utc.index)
    in_dates = cst_date.isin(session_dates_cst)
    in_hours = (mins >= open_min) & (mins <= close_min)

    return df_utc.loc[in_dates & in_hours].copy()

def add_time_columns(df_utc: pd.DataFrame, interval: str) -> pd.DataFrame:
    if df_utc.empty:
        return df_utc

    delta = _interval_to_timedelta(interval)

    # Index is open time in UTC and should be tz-aware UTC.
    open_utc = df_utc.index
    if getattr(open_utc, "tz", None) is None:
        open_utc = open_utc.tz_localize(UTC)
    else:
        open_utc = open_utc.tz_convert(UTC)

    close_utc = open_utc + delta

    # Storage strings (UTC)
    open_utc_str = open_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    close_utc_str = close_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Convert to CST for storage + filtering/debug
    open_cst = open_utc.tz_convert(CST)
    close_cst = close_utc.tz_convert(CST)

    # ISO-8601 with colon in offset: -0600 -> -06:00
    open_cst_str = pd.Index(open_cst.strftime("%Y-%m-%dT%H:%M:%S%z")).str.replace(r"([+-]\d{2})(\d{2})$", r"\1:\2", regex=True)
    close_cst_str = pd.Index(close_cst.strftime("%Y-%m-%dT%H:%M:%S%z")).str.replace(r"([+-]\d{2})(\d{2})$", r"\1:\2", regex=True)

    df = df_utc.copy()
    df["open_ts_utc"] = open_utc_str
    df["close_ts_utc"] = close_utc_str
    df["open_ts_cst"] = open_cst_str
    df["close_ts_cst"] = close_cst_str
    df["cst_date"] = open_cst.strftime("%Y-%m-%d")
    return df


    delta = _interval_to_timedelta(interval)

    # Index is open time in UTC and should be tz-aware UTC.
    open_utc = df_utc.index
    if getattr(open_utc, "tz", None) is None:
        open_utc = open_utc.tz_localize(UTC)
    else:
        open_utc = open_utc.tz_convert(UTC)

    close_utc = open_utc + delta

    # For storage: keep ISO strings; avoid pandas tz-aware -> tz-naive astype issues
    open_utc_str = open_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    close_utc_str = close_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    open_cst = open_utc.tz_convert(CST)
    close_cst = close_utc.tz_convert(CST)

    df = df_utc.copy()
    df["open_ts_utc"] = open_utc_str
    df["close_ts_utc"] = close_utc_str
    df["open_ts_cst"] = open_cst.isoformat()
    df["close_ts_cst"] = close_cst.isoformat()
    df["cst_date"] = open_cst.strftime("%Y-%m-%d")
    return df
