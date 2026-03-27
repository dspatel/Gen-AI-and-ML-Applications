from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable

import requests


def _normalize_data_url(base: str | None) -> str:
    raw = (base or "https://data.alpaca.markets/v2").strip().rstrip("/")
    if "paper-api.alpaca.markets" in raw or raw.endswith("api.alpaca.markets/v2"):
        return "https://data.alpaca.markets/v2"
    if raw.endswith("/v2"):
        return raw
    return f"{raw}/v2"


def _without_version(path: str) -> str:
    raw = str(path or "").strip().rstrip("/")
    if raw.endswith("/v2"):
        return raw[:-3]
    return raw


@dataclass(frozen=True)
class AlpacaCredentials:
    api_key: str
    secret_key: str
    base_url: str
    data_url: str

    @classmethod
    def from_env(cls, env_prefix: str) -> "AlpacaCredentials | None":
        pref = f"{str(env_prefix).strip().upper()}_"
        api_key = os.getenv(f"{pref}ALPACA_API_KEY")
        secret_key = os.getenv(f"{pref}ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            return None
        base_url = (os.getenv(f"{pref}ALPACA_BASE_URL") or "https://paper-api.alpaca.markets/v2").strip().rstrip("/")
        data_url = _normalize_data_url(
            os.getenv(f"{pref}ALPACA_DATA_URL") or os.getenv(f"{pref}ALPACA_BASE_URL") or os.getenv("ALPACA_DATA_URL")
        )
        return cls(api_key=api_key, secret_key=secret_key, base_url=base_url, data_url=data_url)


@dataclass
class AlpacaHttpClient:
    credentials: AlpacaCredentials

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.credentials.api_key,
            "APCA-API-SECRET-KEY": self.credentials.secret_key,
        }

    @property
    def stock_data_url(self) -> str:
        return self.credentials.data_url.rstrip("/")

    @property
    def options_data_url(self) -> str:
        return f"{_without_version(self.credentials.data_url)}/v1beta1/options"

    def _get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
        resp = requests.get(url, headers=self.headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def get_account(self) -> dict[str, Any]:
        url = f"{self.credentials.base_url}/account"
        return self._get(url, timeout=20)

    def get_latest_stock_quote(self, symbol: str, feed: str) -> dict[str, Any]:
        url = f"{self.stock_data_url}/stocks/{str(symbol).strip().upper()}/quotes/latest"
        return self._get(url, params={"feed": feed}, timeout=20)

    def get_stock_bars(
        self,
        symbol: str,
        start: str,
        end: str,
        timeframe: str,
        feed: str,
        adjustment: str = "raw",
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        url = f"{self.stock_data_url}/stocks/bars"
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        symbol_key = str(symbol).strip().upper()
        while True:
            params: dict[str, Any] = {
                "symbols": symbol_key,
                "start": start,
                "end": end,
                "timeframe": timeframe,
                "feed": feed,
                "adjustment": adjustment,
                "limit": int(limit),
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._get(url, params=params)
            rows.extend(payload.get("bars", {}).get(symbol_key, []))
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return rows

    def list_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date_gte: str,
        expiration_date_lte: str,
        contract_type: str | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        status: str | None = "active",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        url = f"{self.credentials.base_url}/options/contracts"
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "underlying_symbols": str(underlying_symbol).strip().upper(),
                "expiration_date_gte": expiration_date_gte,
                "expiration_date_lte": expiration_date_lte,
                "limit": int(limit),
            }
            if status:
                params["status"] = status
            if contract_type:
                params["type"] = str(contract_type).strip().lower()
            if strike_price_gte is not None:
                params["strike_price_gte"] = float(strike_price_gte)
            if strike_price_lte is not None:
                params["strike_price_lte"] = float(strike_price_lte)
            if page_token:
                params["page_token"] = page_token
            payload = self._get(url, params=params)
            rows.extend(payload.get("option_contracts", []))
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return rows

    def get_option_snapshots(
        self,
        underlying_symbol: str,
        feed: str,
        expiration_date: str | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int = 1000,
    ) -> dict[str, dict[str, Any]]:
        url = f"{self.options_data_url}/snapshots/{str(underlying_symbol).strip().upper()}"
        out: dict[str, dict[str, Any]] = {}
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"feed": feed, "limit": int(limit)}
            if expiration_date:
                params["expiration_date"] = expiration_date
            if strike_price_gte is not None:
                params["strike_price_gte"] = float(strike_price_gte)
            if strike_price_lte is not None:
                params["strike_price_lte"] = float(strike_price_lte)
            if page_token:
                params["page_token"] = page_token
            payload = self._get(url, params=params)
            out.update(payload.get("snapshots", {}) or {})
            page_token = payload.get("next_page_token")
            if not page_token:
                break
        return out

    def get_latest_option_quotes(self, symbols: Iterable[str], feed: str) -> dict[str, Any]:
        symbol_list = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        if not symbol_list:
            return {}
        url = f"{self.options_data_url}/quotes/latest"
        payload = self._get(url, params={"symbols": ",".join(symbol_list), "feed": feed}, timeout=30)
        return payload.get("quotes", {}) or {}

    def get_option_bars(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
        timeframe: str,
        limit: int = 10000,
        chunk_size: int = 50,
    ) -> dict[str, list[dict[str, Any]]]:
        symbol_list = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        if not symbol_list:
            return {}
        url = f"{self.options_data_url}/bars"
        out: dict[str, list[dict[str, Any]]] = {}
        for idx in range(0, len(symbol_list), max(1, int(chunk_size))):
            chunk = symbol_list[idx : idx + max(1, int(chunk_size))]
            page_token: str | None = None
            while True:
                params: dict[str, Any] = {
                    "symbols": ",".join(chunk),
                    "start": start,
                    "end": end,
                    "timeframe": timeframe,
                    "limit": int(limit),
                }
                if page_token:
                    params["page_token"] = page_token
                payload = self._get(url, params=params, timeout=60)
                bars_map = payload.get("bars", {}) or {}
                for symbol_key, rows in bars_map.items():
                    out.setdefault(symbol_key, []).extend(rows or [])
                page_token = payload.get("next_page_token")
                if not page_token:
                    break
        return out
