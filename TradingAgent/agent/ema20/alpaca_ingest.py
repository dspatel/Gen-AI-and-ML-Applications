from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal
import requests


UTC = ZoneInfo("UTC")
CST = ZoneInfo("America/Chicago")


def _normalize_data_url(base: str | None) -> str:
    raw = (base or "https://data.alpaca.markets/v2").strip().rstrip("/")
    if "paper-api.alpaca.markets" in raw or raw.endswith("api.alpaca.markets/v2"):
        return "https://data.alpaca.markets/v2"
    if raw.endswith("/v2"):
        return raw
    return f"{raw}/v2"


def _timeframe(interval: str) -> str:
    s = interval.strip().lower()
    if s.endswith("m"):
        return f"{int(s[:-1])}Min"
    if s.endswith("h"):
        return f"{int(s[:-1])}Hour"
    if s == "1d":
        return "1Day"
    raise ValueError(f"Unsupported interval for Alpaca timeframe: {interval}")


def _interval_minutes(interval: str) -> int:
    s = interval.strip().lower()
    if s.endswith("m"):
        return int(s[:-1])
    if s.endswith("h"):
        return int(s[:-1]) * 60
    if s == "1d":
        return 24 * 60
    raise ValueError(f"Unsupported interval: {interval}")


def _session_minutes(session_start: str, session_end: str) -> tuple[int, int]:
    sh, sm = [int(x) for x in session_start.split(":")]
    eh, em = [int(x) for x in session_end.split(":")]
    return sh * 60 + sm, eh * 60 + em


def _fetch_alpaca_intraday(
    symbol: str,
    interval: str,
    start_cst: datetime,
    end_cst: datetime,
    *,
    feed: str = "iex",
) -> pd.DataFrame:
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("Missing ALPACA_API_KEY / ALPACA_SECRET_KEY for EMA20 Alpaca ingestion")

    data_url = _normalize_data_url(os.getenv("ALPACA_DATA_URL") or os.getenv("ALPACA_BASE_URL"))
    url = f"{data_url}/stocks/bars"
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    }
    params = {
        "symbols": symbol,
        "timeframe": _timeframe(interval),
        "start": start_cst.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_cst.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adjustment": "raw",
        "feed": feed,
        "sort": "asc",
        "limit": 10000,
    }

    rows: list[dict] = []
    page_token = None
    while True:
        req = dict(params)
        if page_token:
            req["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=req, timeout=45)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("bars", {}).get(symbol, []))
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame = pd.DataFrame(rows).rename(
        columns={
            "t": "ts",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
        }
    )
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.set_index("ts").sort_index()
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in frame.columns]
    return frame[keep]


def _session_dates(start_date: str, end_date: str) -> list[date]:
    cal = mcal.get_calendar("NYSE")
    sched = cal.schedule(start_date=start_date, end_date=end_date)
    if sched.empty:
        return []
    return [pd.Timestamp(x).date() for x in sched.index]


def _day_windows_for_symbol(
    conn: sqlite3.Connection,
    symbol: str,
    interval: str,
    session_dates: list[date],
) -> list[tuple[str, str]]:
    if not session_dates:
        return []
    req_start = session_dates[0].isoformat()
    req_end = session_dates[-1].isoformat()
    row = conn.execute(
        """
        SELECT DISTINCT cst_date
        FROM candles
        WHERE symbol = ? AND interval = ?
          AND cst_date >= ? AND cst_date <= ?
        ORDER BY cst_date
        """,
        (symbol, interval, req_start, req_end),
    ).fetchall()
    have_sessions = {date.fromisoformat(str(r[0])) for r in row}
    missing = [d for d in session_dates if d not in have_sessions]
    if not missing:
        return []

    windows: list[tuple[str, str]] = []
    run_start: date | None = None
    run_end: date | None = None
    missing_set = set(missing)
    for d in session_dates:
        if d in missing_set:
            if run_start is None:
                run_start = d
            run_end = d
        elif run_start is not None and run_end is not None:
            windows.append((run_start.isoformat(), run_end.isoformat()))
            run_start = None
            run_end = None
    if run_start is not None and run_end is not None:
        windows.append((run_start.isoformat(), run_end.isoformat()))
    return windows


def ingest_from_alpaca(
    conn: sqlite3.Connection,
    symbols: list[str],
    interval: str,
    start_date: str,
    end_date: str,
    *,
    session_start: str,
    session_end: str,
    feed: str = "iex",
) -> dict[str, int | str]:
    open_min, close_min = _session_minutes(session_start, session_end)
    delta = pd.Timedelta(minutes=_interval_minutes(interval))
    sessions = _session_dates(start_date, end_date)

    fetched_rows = 0
    upsert_rows = 0
    symbols_fetched = 0
    windows_fetched = 0

    for symbol in symbols:
        windows = _day_windows_for_symbol(conn, symbol, interval, sessions)
        if not windows:
            continue
        symbols_fetched += 1

        for ws, we in windows:
            windows_fetched += 1
            start_cst = datetime.fromisoformat(f"{ws}T{session_start}:00").replace(tzinfo=CST)
            end_cst = datetime.fromisoformat(f"{we}T{session_end}:00").replace(tzinfo=CST)

            bars = _fetch_alpaca_intraday(symbol, interval, start_cst, end_cst, feed=feed)
            if bars.empty:
                continue
            fetched_rows += int(len(bars))

            df = bars.reset_index().rename(columns={"ts": "open_ts_utc"})
            df["open_ts_utc"] = pd.to_datetime(df["open_ts_utc"], utc=True)
            df["close_ts_utc"] = df["open_ts_utc"] + delta
            df["open_ts_cst"] = df["open_ts_utc"].dt.tz_convert(CST)
            mins = df["open_ts_cst"].dt.hour * 60 + df["open_ts_cst"].dt.minute
            df = df[(mins >= open_min) & (mins < close_min)].copy()
            if df.empty:
                continue

            df["close_ts_cst"] = df["close_ts_utc"].dt.tz_convert(CST)
            df["cst_date"] = df["open_ts_cst"].dt.strftime("%Y-%m-%d")
            df = df[(df["cst_date"] >= start_date) & (df["cst_date"] <= end_date)].copy()
            if df.empty:
                continue

            now_cst = datetime.now(CST).isoformat()
            rows = [
                (
                    symbol,
                    interval,
                    str(r.open_ts_utc),
                    str(r.close_ts_utc),
                    str(r.open_ts_cst),
                    str(r.close_ts_cst),
                    str(r.cst_date),
                    float(r.open),
                    float(r.high),
                    float(r.low),
                    float(r.close),
                    (None if pd.isna(r.volume) else float(r.volume)),
                    f"alpaca:{feed}",
                    now_cst,
                )
                for r in df.itertuples(index=False)
            ]
            conn.executemany(
                """
                INSERT OR REPLACE INTO candles(
                    symbol, interval, open_ts_utc, close_ts_utc,
                    open_ts_cst, close_ts_cst, cst_date,
                    open, high, low, close, volume, source, ingested_at_cst
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            upsert_rows += int(len(rows))

    conn.commit()
    return {
        "provider": "alpaca",
        "feed": feed,
        "symbols_requested": int(len(symbols)),
        "symbols_fetched": int(symbols_fetched),
        "windows_fetched": int(windows_fetched),
        "fetched_rows": int(fetched_rows),
        "upsert_rows": int(upsert_rows),
    }
