from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from .db import CandleRow, upsert_candles
from .alpaca_client import fetch_alpaca_intraday
from .yf_client import fetch_yf_intraday, filter_to_regular_session_cst, sanitize_intraday_bars, add_time_columns

CST = ZoneInfo("America/Chicago")

def run_ingest(
    conn,
    symbol: str,
    interval: str,
    start_cst: datetime,
    end_cst: datetime,
    session_dates_cst: list[str],
    session_start: str,
    session_end: str,
    provider: str = "alpaca",
    alpaca_feed: str = "iex",
):
    src = provider.strip().lower()
    if src == "alpaca":
        df = fetch_alpaca_intraday(symbol, interval, start_cst, end_cst, feed=alpaca_feed)
        source = f"alpaca:{alpaca_feed}"
    elif src == "yahoo":
        df = fetch_yf_intraday(symbol, interval, start_cst, end_cst)
        source = "yfinance"
    elif src == "auto":
        try:
            df = fetch_alpaca_intraday(symbol, interval, start_cst, end_cst, feed=alpaca_feed)
            source = f"alpaca:{alpaca_feed}"
        except Exception:
            df = fetch_yf_intraday(symbol, interval, start_cst, end_cst)
            source = "yfinance"
    else:
        raise ValueError(f"Unsupported ingestion provider: {provider}")

    raw = 0 if df is None else len(df)

    df = filter_to_regular_session_cst(df, session_dates_cst, session_start, session_end)
    filtered = 0 if df is None else len(df)

    df = sanitize_intraday_bars(df, interval, drop_zero_volume=(source == "yfinance"))
    sanitized = 0 if df is None else len(df)

    df = add_time_columns(df, interval)
    if df is None or df.empty:
        return raw, sanitized, 0, 0

    now_cst = datetime.now(CST).isoformat()

    rows = []
    for _, r in df.iterrows():
        vol = None
        try:
            v = r.get("volume", None)
            if v is not None and pd.notna(v):
                vol = float(v)
        except Exception:
            vol = None

        rows.append(CandleRow(
            symbol=symbol,
            interval=interval,
            open_ts_utc=str(r["open_ts_utc"]),
            close_ts_utc=str(r["close_ts_utc"]),
            open_ts_cst=str(r["open_ts_cst"]),
            close_ts_cst=str(r["close_ts_cst"]),
            cst_date=str(r["cst_date"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=vol,
            source=source,
            ingested_at_cst=now_cst,
        ))

    ins, sk = upsert_candles(conn, rows)
    return raw, sanitized, ins, sk
