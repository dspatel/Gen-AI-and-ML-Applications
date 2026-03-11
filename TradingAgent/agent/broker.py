from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class AlpacaTradingClient:
    api_key: str
    secret_key: str
    base_url: str = "https://paper-api.alpaca.markets/v2"

    @classmethod
    def from_env(cls, env_prefix: str | None = None) -> "AlpacaTradingClient | None":
        """Load credentials from environment.

        Priority:
        1) Prefixed vars if env_prefix is provided, e.g. ORB_ALPACA_API_KEY
        2) Generic vars, e.g. ALPACA_API_KEY
        """
        prefixes: list[str] = []
        if env_prefix and str(env_prefix).strip():
            prefixes.append(f"{str(env_prefix).strip().upper()}_")
        prefixes.append("")

        for pref in prefixes:
            api_key = os.getenv(f"{pref}ALPACA_API_KEY")
            secret_key = os.getenv(f"{pref}ALPACA_SECRET_KEY")
            base_url = os.getenv(f"{pref}ALPACA_BASE_URL") or os.getenv(f"{pref}ALPACA_API_URL")
            if api_key and secret_key:
                if not base_url:
                    base_url = os.getenv("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets/v2"
                return cls(api_key=api_key, secret_key=secret_key, base_url=base_url.rstrip("/"))
        return None

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/account")

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/positions/{symbol}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def list_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"status": "open"}
        if symbol:
            params["symbols"] = symbol
        return self._request("GET", "/orders", params=params)

    def submit_market_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "side": side.lower(),
            "type": "market",
            "time_in_force": "day",
            "qty": str(int(qty)),
            "client_order_id": client_order_id,
        }
        return self._request("POST", "/orders", json=payload)

    def submit_stop_order(self, symbol: str, side: str, qty: int, stop_price: float, client_order_id: str) -> dict[str, Any]:
        payload = {
            "symbol": symbol,
            "side": side.lower(),
            "type": "stop",
            "time_in_force": "day",
            "qty": str(int(qty)),
            "stop_price": f"{float(stop_price):.2f}",
            "client_order_id": client_order_id,
        }
        return self._request("POST", "/orders", json=payload)

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/orders/{order_id}")

    def close_position(self, symbol: str) -> dict[str, Any]:
        return self._request("DELETE", f"/positions/{symbol}")

    def list_positions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/positions")

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }
        resp = requests.request(method=method, url=url, headers=headers, params=params, json=json, timeout=30)
        if resp.status_code >= 400:
            raise requests.HTTPError(f"Alpaca API error {resp.status_code}: {resp.text}", response=resp)
        if resp.text.strip() == "":
            return {}
        return resp.json()
