from __future__ import annotations

from .config_loader import OptionsConfig
from .contract_selector import select_contract
from .models import OptionContractSnapshot, PortfolioState, TradePlan, TradeRejection, UnderlyingSignal


def _cap_dollars(account_equity: float, pct_cap: float, dollar_cap: float) -> float:
    pct_value = float(account_equity) * float(pct_cap)
    if pct_cap <= 0 and dollar_cap <= 0:
        return 0.0
    if pct_cap <= 0:
        return float(dollar_cap)
    if dollar_cap <= 0:
        return float(pct_value)
    return float(min(pct_value, dollar_cap))


def _per_trade_cap_values(cfg: OptionsConfig, symbol: str | None = None) -> tuple[float, float, bool]:
    override = None if not symbol else cfg.symbol_risk_overrides.get(str(symbol).strip().upper())
    pct_cap = (
        cfg.max_premium_risk_per_trade_pct
        if override is None or override.max_premium_risk_per_trade_pct is None
        else float(override.max_premium_risk_per_trade_pct)
    )
    dollar_cap = (
        cfg.max_premium_risk_per_trade_dollars
        if override is None or override.max_premium_risk_per_trade_dollars is None
        else float(override.max_premium_risk_per_trade_dollars)
    )
    return float(pct_cap), float(dollar_cap), bool(override is not None)


def max_premium_budget(account_equity: float, cfg: OptionsConfig, symbol: str | None = None) -> float:
    pct_cap, dollar_cap, _ = _per_trade_cap_values(cfg=cfg, symbol=symbol)
    return _cap_dollars(
        account_equity=account_equity,
        pct_cap=pct_cap,
        dollar_cap=dollar_cap,
    )


def budget_context(
    account_equity: float,
    cfg: OptionsConfig,
    portfolio_state: PortfolioState | None = None,
    symbol: str | None = None,
) -> dict[str, float | int | str | None]:
    state = portfolio_state or PortfolioState()
    pct_cap, dollar_cap, override_applied = _per_trade_cap_values(cfg=cfg, symbol=symbol)
    per_trade_cap = max_premium_budget(account_equity=account_equity, cfg=cfg, symbol=symbol)
    total_open_limit = _cap_dollars(
        account_equity=account_equity,
        pct_cap=cfg.max_total_open_premium_pct,
        dollar_cap=cfg.max_total_open_premium_dollars,
    )
    symbol_open_limit = _cap_dollars(
        account_equity=account_equity,
        pct_cap=cfg.max_symbol_open_premium_pct,
        dollar_cap=cfg.max_symbol_open_premium_dollars,
    )
    if override_applied:
        symbol_open_limit = max(float(symbol_open_limit), float(per_trade_cap))
    direction_open_limit = _cap_dollars(
        account_equity=account_equity,
        pct_cap=cfg.max_direction_open_premium_pct,
        dollar_cap=cfg.max_direction_open_premium_dollars,
    )
    cash_reserve_required = float(account_equity) * float(cfg.min_cash_reserve_pct)
    cash_available = None if state.cash_available is None else float(state.cash_available)
    cash_deployable = None if cash_available is None else max(0.0, cash_available - cash_reserve_required)
    total_open_remaining = max(0.0, total_open_limit - float(state.open_premium_total))
    symbol_open_remaining = max(0.0, symbol_open_limit - float(state.open_symbol_premium))
    direction_open_remaining = max(0.0, direction_open_limit - float(state.open_direction_premium))
    daily_loss_limit_dollars = float(account_equity) * float(cfg.daily_loss_limit_pct)
    effective_budget = min(
        per_trade_cap,
        total_open_remaining,
        symbol_open_remaining,
        direction_open_remaining,
        float("inf") if cash_deployable is None else cash_deployable,
    )
    return {
        "account_equity": float(account_equity),
        "symbol": None if symbol is None else str(symbol).strip().upper(),
        "symbol_override_applied": int(override_applied),
        "per_trade_cap_pct": float(pct_cap),
        "per_trade_cap_dollar_limit": float(dollar_cap),
        "cash_available": cash_available,
        "cash_reserve_required": cash_reserve_required,
        "cash_deployable": cash_deployable,
        "per_trade_cap_dollars": per_trade_cap,
        "total_open_limit_dollars": total_open_limit,
        "total_open_remaining_dollars": total_open_remaining,
        "symbol_open_limit_dollars": symbol_open_limit,
        "symbol_open_remaining_dollars": symbol_open_remaining,
        "direction_open_limit_dollars": direction_open_limit,
        "direction_open_remaining_dollars": direction_open_remaining,
        "daily_loss_limit_dollars": daily_loss_limit_dollars,
        "realized_pnl_today": float(state.realized_pnl_today),
        "new_trades_today": int(state.new_trades_today),
        "max_new_trades_per_day": int(cfg.max_new_trades_per_day),
        "effective_budget_dollars": float(effective_budget),
    }


def contracts_for_budget(
    premium_per_contract: float,
    account_equity: float,
    cfg: OptionsConfig,
    portfolio_state: PortfolioState | None = None,
    symbol: str | None = None,
) -> int:
    cost_per_contract = float(premium_per_contract) * 100.0
    if cost_per_contract <= 0:
        return 0
    budget = float(
        budget_context(account_equity=account_equity, cfg=cfg, portfolio_state=portfolio_state, symbol=symbol)[
            "effective_budget_dollars"
        ]
        or 0.0
    )
    raw = int(budget // cost_per_contract)
    return max(0, min(int(cfg.max_contracts_per_trade), raw))


def build_trade_plan(
    signal: UnderlyingSignal,
    chain: list[OptionContractSnapshot],
    account_equity: float,
    cfg: OptionsConfig,
    portfolio_state: PortfolioState | None = None,
) -> TradePlan | TradeRejection:
    evaluated, selected = select_contract(signal=signal, contracts=chain, cfg=cfg)
    if selected is None:
        reject_reason = "no_contract_passed_filters"
        if evaluated:
            reasons = sorted({str(r.reject_reason) for r in evaluated if r.reject_reason})
            reject_reason = f"{reject_reason}: {','.join(reasons)}"
        return TradeRejection(signal=signal, reason=reject_reason)

    budget = budget_context(account_equity=account_equity, cfg=cfg, portfolio_state=portfolio_state, symbol=signal.symbol)
    if int(budget["new_trades_today"] or 0) >= int(budget["max_new_trades_per_day"] or 0):
        return TradeRejection(signal=signal, reason="daily_trade_count_limit", context=budget)
    if float(budget["realized_pnl_today"] or 0.0) <= -float(budget["daily_loss_limit_dollars"] or 0.0):
        return TradeRejection(signal=signal, reason="daily_loss_limit_reached", context=budget)
    if float(budget["total_open_remaining_dollars"] or 0.0) <= 0:
        return TradeRejection(signal=signal, reason="max_total_open_premium_reached", context=budget)
    if float(budget["symbol_open_remaining_dollars"] or 0.0) <= 0:
        return TradeRejection(signal=signal, reason="max_symbol_open_premium_reached", context=budget)
    if float(budget["direction_open_remaining_dollars"] or 0.0) <= 0:
        return TradeRejection(signal=signal, reason="max_direction_open_premium_reached", context=budget)
    cash_deployable = budget["cash_deployable"]
    if cash_deployable is not None and float(cash_deployable) <= 0:
        return TradeRejection(signal=signal, reason="cash_reserve_lock", context=budget)

    premium = selected.contract.mid
    qty = contracts_for_budget(
        premium_per_contract=premium,
        account_equity=account_equity,
        cfg=cfg,
        portfolio_state=portfolio_state,
        symbol=signal.symbol,
    )
    if qty <= 0:
        return TradeRejection(signal=signal, reason="premium_budget_too_small_for_selected_contract", context=budget)

    total_risk = float(qty) * float(premium) * 100.0
    return TradePlan(
        signal=signal,
        contract=selected.contract,
        contracts=qty,
        premium_per_contract=float(premium),
        premium_at_risk_total=total_risk,
        max_budget_dollars=float(budget["effective_budget_dollars"] or 0.0),
        per_trade_budget_dollars=float(budget["per_trade_cap_dollars"] or 0.0),
        selection_reason=selected.selection_reason,
        budget_context=budget,
    )
