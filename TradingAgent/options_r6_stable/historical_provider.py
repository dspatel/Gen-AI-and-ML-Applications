from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .alpaca import AlpacaCredentials, AlpacaHttpClient
from .config_loader import AppConfig


@dataclass(frozen=True)
class HistoricalProviderCapabilities:
    name: str
    implemented: bool
    supports_intraday_stock_bars: bool
    supports_intraday_option_bars: bool
    supports_archived_contract_discovery: bool
    notes: str


class HistoricalDataProvider(Protocol):
    name: str
    capabilities: HistoricalProviderCapabilities

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
        ...

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
        ...

    def get_option_bars(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
        timeframe: str,
        limit: int = 10000,
        chunk_size: int = 50,
    ) -> dict[str, list[dict[str, Any]]]:
        ...


@dataclass
class AlpacaHistoricalProvider:
    cfg: AppConfig
    client: AlpacaHttpClient

    name: str = "alpaca"
    capabilities: HistoricalProviderCapabilities = HistoricalProviderCapabilities(
        name="alpaca",
        implemented=True,
        supports_intraday_stock_bars=True,
        supports_intraday_option_bars=True,
        supports_archived_contract_discovery=True,
        notes="Current default provider. Historical staging now supports expired-contract discovery via inactive-status contract lookup; execution realism is still bar-proxied.",
    )

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "AlpacaHistoricalProvider":
        creds = AlpacaCredentials.from_env(cfg.options.paper_account_env_prefix)
        if creds is None:
            raise RuntimeError(f"Missing Alpaca credentials for prefix {cfg.options.paper_account_env_prefix}")
        return cls(cfg=cfg, client=AlpacaHttpClient(creds))

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
        return self.client.get_stock_bars(
            symbol=symbol,
            start=start,
            end=end,
            timeframe=timeframe,
            feed=feed,
            adjustment=adjustment,
            limit=limit,
        )

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
        return self.client.list_option_contracts(
            underlying_symbol=underlying_symbol,
            expiration_date_gte=expiration_date_gte,
            expiration_date_lte=expiration_date_lte,
            contract_type=contract_type,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            status=status,
            limit=limit,
        )

    def get_option_bars(
        self,
        symbols: Iterable[str],
        start: str,
        end: str,
        timeframe: str,
        limit: int = 10000,
        chunk_size: int = 50,
    ) -> dict[str, list[dict[str, Any]]]:
        return self.client.get_option_bars(
            symbols=symbols,
            start=start,
            end=end,
            timeframe=timeframe,
            limit=limit,
            chunk_size=chunk_size,
        )


def _provider_manifest() -> dict[str, HistoricalProviderCapabilities]:
    return {
        "alpaca": AlpacaHistoricalProvider.capabilities,
        "polygon": HistoricalProviderCapabilities(
            name="polygon",
            implemented=False,
            supports_intraday_stock_bars=False,
            supports_intraday_option_bars=False,
            supports_archived_contract_discovery=False,
            notes="Reserved provider slot for future archived historical options integration.",
        ),
        "databento": HistoricalProviderCapabilities(
            name="databento",
            implemented=False,
            supports_intraday_stock_bars=False,
            supports_intraday_option_bars=False,
            supports_archived_contract_discovery=False,
            notes="Reserved provider slot for future archived historical options integration.",
        ),
    }


def list_historical_provider_capabilities() -> dict[str, HistoricalProviderCapabilities]:
    return _provider_manifest()


def build_historical_provider(cfg: AppConfig) -> HistoricalDataProvider:
    provider_name = str(cfg.market_data.historical_provider).strip().lower()
    if provider_name == "alpaca":
        return AlpacaHistoricalProvider.from_config(cfg)
    manifest = _provider_manifest()
    if provider_name in manifest:
        raise NotImplementedError(
            f"Historical provider '{provider_name}' is configured but not implemented yet. "
            "The abstraction is now in place so we can add it cleanly next."
        )
    raise ValueError(f"Unsupported historical provider: {provider_name}")
