from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


SignalDirection = Literal["BULLISH", "BEARISH"]
OptionRight = Literal["call", "put"]


@dataclass(frozen=True)
class UnderlyingSignal:
    symbol: str
    direction: SignalDirection
    event_ts: str
    variant_id: str
    event_id: str | None = None
    confidence: float | None = None
    ref_horizon: int | None = None
    include_today_or: int | None = None
    underlying_price: float | None = None
    underlying_stop_price: float | None = None
    bar_open: float | None = None
    bar_high: float | None = None
    bar_low: float | None = None
    bar_close: float | None = None
    ema20: float | None = None
    ema20_slope: float | None = None
    source_tag: str | None = None
    notes_json: str | None = None


@dataclass(frozen=True)
class PortfolioState:
    cash_available: float | None = None
    open_premium_total: float = 0.0
    open_symbol_premium: float = 0.0
    open_direction_premium: float = 0.0
    realized_pnl_today: float = 0.0
    new_trades_today: int = 0


@dataclass(frozen=True)
class OptionContractSnapshot:
    option_symbol: str
    underlying_symbol: str
    right: OptionRight
    expiration_date: str
    strike: float
    dte: int
    bid: float
    ask: float
    delta: float | None
    open_interest: int | None
    volume: int | None
    last: float | None = None
    iv: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    @property
    def mid(self) -> float:
        return (float(self.bid) + float(self.ask)) / 2.0

    @property
    def spread(self) -> float:
        return float(self.ask) - float(self.bid)

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        if mid <= 0:
            return float("inf")
        return self.spread / mid


@dataclass(frozen=True)
class ContractFilterResult:
    contract: OptionContractSnapshot
    passed: bool
    reject_reason: str | None
    score: tuple[float, float, float, float]
    filter_flags: dict[str, bool]
    score_details: dict[str, float]


@dataclass(frozen=True)
class SelectedContract:
    contract: OptionContractSnapshot
    selection_reason: str


@dataclass(frozen=True)
class TradePlan:
    signal: UnderlyingSignal
    contract: OptionContractSnapshot
    contracts: int
    premium_per_contract: float
    premium_at_risk_total: float
    max_budget_dollars: float
    per_trade_budget_dollars: float
    selection_reason: str
    budget_context: dict[str, Any]


@dataclass(frozen=True)
class TradeRejection:
    signal: UnderlyingSignal
    reason: str
    context: dict[str, Any] | None = None
