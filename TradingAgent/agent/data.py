from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from agent.config import CHICAGO_TZ, SESSION_END, SESSION_START


@dataclass
class Alpaca5mClient:
    api_key: str
    secret_key: str
    data_url: str = "https://data.alpaca.markets/v2"
    feed: str = "iex"

    @classmethod
    def from_env(cls) -> "Alpaca5mClient | None":
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        base = os.getenv("ALPACA_DATA_URL") or os.getenv("ALPACA_BASE_URL") or "https://data.alpaca.markets/v2"
        normalized = base.rstrip("/")
        if "paper-api.alpaca.markets" in normalized or normalized.endswith("api.alpaca.markets/v2"):
            base = "https://data.alpaca.markets/v2"
        if not api_key or not secret_key:
            return None
        return cls(api_key=api_key, secret_key=secret_key, data_url=base)

    def fetch_intraday(self, symbol: str, start: str, end: str, timeframe_min: int = 5) -> pd.DataFrame:
        url = f"{self.data_url.rstrip('/')}/stocks/bars"
        tf = max(1, int(timeframe_min))
        params = {
            "symbols": symbol,
            "timeframe": f"{tf}Min",
            "start": f"{start}T00:00:00Z",
            "end": f"{end}T23:59:59Z",
            "adjustment": "raw",
            "feed": self.feed,
            "sort": "asc",
            "limit": 10000,
        }
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

        rows: list[dict] = []
        token = None
        while True:
            req = dict(params)
            if token:
                req["page_token"] = token
            resp = requests.get(url, headers=headers, params=req, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            rows.extend(payload.get("bars", {}).get(symbol, []))
            token = payload.get("next_page_token")
            if not token:
                break

        if not rows:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume"])

        frame = pd.DataFrame(rows)
        frame = frame.rename(columns={"t": "ts", "v": "volume"})
        frame = frame[["ts", "o", "h", "l", "c", "volume"]]
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(CHICAGO_TZ)

        mask = (frame["ts"].dt.time >= SESSION_START) & (frame["ts"].dt.time <= SESSION_END)
        frame = frame.loc[mask].copy()
        frame["symbol"] = symbol
        frame["ts"] = frame["ts"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
        return frame[["symbol", "ts", "o", "h", "l", "c", "volume"]]

    def fetch_5m(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        return self.fetch_intraday(symbol=symbol, start=start, end=end, timeframe_min=5)


@dataclass
class Synthetic5mFactory:
    seed: int = 42

    def generate(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        dates = pd.bdate_range(start=start, end=end, freq="C")
        if len(dates) == 0:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume"])

        symbol_seed = sum((i + 1) * ord(ch) for i, ch in enumerate(symbol))
        rng = np.random.default_rng((self.seed + symbol_seed) % (2**32))
        rows: list[dict] = []
        base = 100.0

        for d_idx, d in enumerate(dates):
            day = d.tz_localize(CHICAGO_TZ)
            start_ts = day + pd.Timedelta(hours=8, minutes=30)
            n = 78

            daily_drift = 0.02 if ((d_idx // 12) % 2 == 0) else -0.02
            trend_bias = rng.normal(0, 0.01)
            price = base + rng.normal(0, 0.4)

            for i in range(n):
                ts = start_ts + pd.Timedelta(minutes=5 * i)
                # Slightly higher volatility near open and close.
                vol_scale = 1.4 if i < 12 or i > 62 else 1.0
                shock = rng.normal(0, 0.14 * vol_scale)
                drift = daily_drift + trend_bias

                open_px = price
                close_px = max(1.0, open_px + drift + shock)
                spread = abs(rng.normal(0.08 * vol_scale, 0.03))
                high_px = max(open_px, close_px) + spread
                low_px = min(open_px, close_px) - spread
                volume = max(100.0, rng.normal(2200 * vol_scale, 600))

                rows.append(
                    {
                        "symbol": symbol,
                        "ts": ts,
                        "o": float(open_px),
                        "h": float(high_px),
                        "l": float(low_px),
                        "c": float(close_px),
                        "volume": float(volume),
                    }
                )
                price = close_px
            base = price

        frame = pd.DataFrame(rows).sort_values("ts")
        frame["ts"] = frame["ts"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
        return frame[["symbol", "ts", "o", "h", "l", "c", "volume"]]


@dataclass
class Yahoo5mClient:
    interval: str = "5m"
    max_chunk_days: int = 59

    def fetch_5m(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        start_ts = pd.Timestamp(start).date()
        end_ts = pd.Timestamp(end).date()
        if end_ts < start_ts:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume"])

        parts: list[pd.DataFrame] = []
        cur = start_ts
        while cur <= end_ts:
            chunk_end = min(cur + pd.Timedelta(days=self.max_chunk_days), end_ts + pd.Timedelta(days=1))
            raw = yf.download(
                tickers=symbol,
                start=pd.Timestamp(cur).strftime("%Y-%m-%d"),
                end=pd.Timestamp(chunk_end).strftime("%Y-%m-%d"),
                interval=self.interval,
                auto_adjust=False,
                prepost=False,
                actions=False,
                progress=False,
                threads=False,
            )
            # Yahoo "end" is exclusive; next chunk starts exactly at previous chunk end.
            cur = pd.Timestamp(chunk_end).date()
            if raw is None or raw.empty:
                continue

            df = raw.copy()
            if isinstance(df.columns, pd.MultiIndex):
                # yfinance can return (Price, Ticker) columns for single ticker downloads.
                df.columns = [c[0] if isinstance(c, tuple) else str(c) for c in df.columns]
            df = df.rename(
                columns={
                    "Open": "o",
                    "High": "h",
                    "Low": "l",
                    "Close": "c",
                    "Volume": "volume",
                }
            )
            required = ["o", "h", "l", "c", "volume"]
            missing = [c for c in required if c not in df.columns]
            if missing:
                continue
            df = df[required].dropna().copy()
            df = df.reset_index().rename(columns={df.index.name or "index": "ts"})
            if "Datetime" in df.columns:
                df = df.rename(columns={"Datetime": "ts"})
            if "Date" in df.columns:
                df = df.rename(columns={"Date": "ts"})
            if "ts" not in df.columns:
                continue
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            parts.append(df)

        if not parts:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume"])

        frame = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
        frame["ts"] = frame["ts"].dt.tz_convert(CHICAGO_TZ)
        mask = (frame["ts"].dt.time >= SESSION_START) & (frame["ts"].dt.time <= SESSION_END)
        frame = frame.loc[mask].copy()
        if frame.empty:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume"])

        # Yahoo can emit zero-volume placeholders intraday; drop them for live/paper signal integrity.
        for col in ("o", "h", "l", "c", "volume"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["o", "h", "l", "c", "volume"])
        frame = frame.loc[frame["volume"] > 0].copy()
        frame = frame.loc[
            (frame["h"] >= frame["l"])
            & (frame["h"] >= frame["o"])
            & (frame["h"] >= frame["c"])
            & (frame["l"] <= frame["o"])
            & (frame["l"] <= frame["c"])
            & (frame["o"] > 0)
            & (frame["h"] > 0)
            & (frame["l"] > 0)
            & (frame["c"] > 0)
        ].copy()
        if frame.empty:
            return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume"])

        frame["symbol"] = symbol
        frame["ts"] = frame["ts"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
        return frame[["symbol", "ts", "o", "h", "l", "c", "volume"]]


def load_5m_data(symbol: str, start: str, end: str, provider: str) -> tuple[pd.DataFrame, str]:
    return load_intraday_data(symbol=symbol, start=start, end=end, provider=provider, timeframe_min=5)


def load_intraday_data(symbol: str, start: str, end: str, provider: str, timeframe_min: int = 5) -> tuple[pd.DataFrame, str]:
    tf = max(1, int(timeframe_min))
    yf_interval = f"{tf}m"
    p = provider.lower()
    if p == "synthetic":
        if tf == 5:
            return Synthetic5mFactory(seed=42).generate(symbol=symbol, start=start, end=end), "synthetic"
        synthetic_5m = Synthetic5mFactory(seed=42).generate(symbol=symbol, start=start, end=end)
        if synthetic_5m.empty:
            return synthetic_5m, "synthetic"
        frame = synthetic_5m.copy()
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        g = (
            frame.set_index("ts")
            .resample(f"{tf}min")
            .agg({"o": "first", "h": "max", "l": "min", "c": "last", "volume": "sum"})
            .dropna()
            .reset_index()
        )
        g["symbol"] = symbol
        g["ts"] = g["ts"].dt.tz_convert(CHICAGO_TZ).dt.strftime("%Y-%m-%d %H:%M:%S%z")
        return g[["symbol", "ts", "o", "h", "l", "c", "volume"]], "synthetic"
    if p == "yahoo":
        data = Yahoo5mClient(interval=yf_interval).fetch_5m(symbol=symbol, start=start, end=end)
        return data, "yahoo"

    if p in {"alpaca", "auto"}:
        client = Alpaca5mClient.from_env()
        if client is None and p == "alpaca":
            raise RuntimeError("Alpaca requested but environment credentials are missing")
        if client is not None:
            data = client.fetch_intraday(symbol=symbol, start=start, end=end, timeframe_min=tf)
            if not data.empty:
                return data, "alpaca"

    if p == "auto":
        data = Yahoo5mClient(interval=yf_interval).fetch_5m(symbol=symbol, start=start, end=end)
        if not data.empty:
            return data, "yahoo"

    return load_intraday_data(symbol=symbol, start=start, end=end, provider="synthetic", timeframe_min=tf)
