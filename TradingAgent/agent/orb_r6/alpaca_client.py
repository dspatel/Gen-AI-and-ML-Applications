from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests


UTC = ZoneInfo("UTC")


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


def fetch_alpaca_intraday(
    symbol: str,
    interval: str,
    start_cst: datetime,
    end_cst: datetime,
    *,
    feed: str = "iex",
    env_prefix: str | None = None,
) -> pd.DataFrame:
    prefixes: list[str] = []
    if env_prefix and str(env_prefix).strip():
        prefixes.append(f"{str(env_prefix).strip().upper()}_")
    prefixes.append("")

    api_key = None
    secret_key = None
    data_url = None
    for pref in prefixes:
        api_key = os.getenv(f"{pref}ALPACA_API_KEY")
        secret_key = os.getenv(f"{pref}ALPACA_SECRET_KEY")
        if api_key and secret_key:
            data_url = _normalize_data_url(
                os.getenv(f"{pref}ALPACA_DATA_URL")
                or os.getenv(f"{pref}ALPACA_BASE_URL")
                or os.getenv("ALPACA_DATA_URL")
                or os.getenv("ALPACA_BASE_URL")
            )
            break
    if not api_key or not secret_key:
        raise RuntimeError("Missing agent-scoped Alpaca credentials for Alpaca historical ingestion")
    if not data_url:
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
        resp = requests.get(url, headers=headers, params=req, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("bars", {}).get(symbol, []))
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame = pd.DataFrame(rows)
    frame = frame.rename(
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
