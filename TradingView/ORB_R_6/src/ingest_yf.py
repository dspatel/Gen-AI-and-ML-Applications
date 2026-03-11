from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from .db import CandleRow, upsert_candles
from .yf_client import fetch_yf_intraday, filter_to_regular_session_cst, add_time_columns

CST = ZoneInfo("America/Chicago")

def run_ingest(conn, symbol: str, interval: str, start_cst: datetime, end_cst: datetime, session_dates_cst: list[str], session_start: str, session_end: str):
    df = fetch_yf_intraday(symbol, interval, start_cst, end_cst)
    raw = 0 if df is None else len(df)

    df = filter_to_regular_session_cst(df, session_dates_cst, session_start, session_end)
    filtered = 0 if df is None else len(df)

    df = add_time_columns(df, interval)
    if df is None or df.empty:
        return raw, filtered, 0, 0

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
            source="yfinance",
            ingested_at_cst=now_cst,
        ))

    ins, sk = upsert_candles(conn, rows)
    return raw, filtered, ins, sk
