from __future__ import annotations

from .config_loader import OptionsConfig
from .models import ContractFilterResult, OptionContractSnapshot, SelectedContract, UnderlyingSignal


def required_right(direction: str) -> str:
    d = str(direction).upper().strip()
    if d == "BULLISH":
        return "call"
    if d == "BEARISH":
        return "put"
    raise ValueError(f"Unsupported direction: {direction!r}")


def _score(contract: OptionContractSnapshot, cfg: OptionsConfig) -> tuple[float, float, float, float]:
    delta = abs(float(contract.delta or 0.0))
    delta_distance = abs(delta - float(cfg.target_delta_preference))
    spread_penalty = float(contract.spread_pct)
    oi_bonus = -float(contract.open_interest or 0)
    volume_bonus = -float(contract.volume or 0)
    return (delta_distance, spread_penalty, oi_bonus, volume_bonus)


def _score_details(contract: OptionContractSnapshot, cfg: OptionsConfig) -> dict[str, float]:
    score = _score(contract, cfg)
    return {
        "delta_distance": float(score[0]),
        "spread_penalty": float(score[1]),
        "open_interest_bonus": float(score[2]),
        "volume_bonus": float(score[3]),
        "composite_hint": float(score[0]) + float(score[1]),
    }


def _filter_flags(signal: UnderlyingSignal, contract: OptionContractSnapshot, cfg: OptionsConfig) -> dict[str, bool]:
    right = required_right(signal.direction)
    delta_abs = abs(float(contract.delta or 0.0))
    return {
        "right_ok": contract.right == right,
        "dte_min_ok": contract.dte >= int(cfg.allowed_dte_min),
        "dte_max_ok": contract.dte <= int(cfg.allowed_dte_max),
        "bid_positive_ok": contract.bid > 0,
        "ask_positive_ok": contract.ask > 0,
        "market_not_crossed_ok": contract.ask >= contract.bid,
        "mid_positive_ok": contract.mid > 0,
        "spread_ok": contract.spread_pct <= float(cfg.max_spread_pct),
        "delta_present_ok": contract.delta is not None,
        "delta_min_ok": delta_abs >= float(cfg.target_delta_min),
        "delta_max_ok": delta_abs <= float(cfg.target_delta_max),
        "open_interest_ok": int(contract.open_interest or 0) >= int(cfg.min_open_interest),
        "volume_ok": int(contract.volume or 0) >= int(cfg.min_contract_volume),
    }


def _result(
    signal: UnderlyingSignal,
    contract: OptionContractSnapshot,
    cfg: OptionsConfig,
    passed: bool,
    reject_reason: str | None,
) -> ContractFilterResult:
    return ContractFilterResult(
        contract=contract,
        passed=passed,
        reject_reason=reject_reason,
        score=_score(contract, cfg),
        filter_flags=_filter_flags(signal, contract, cfg),
        score_details=_score_details(contract, cfg),
    )


def evaluate_contract(
    signal: UnderlyingSignal,
    contract: OptionContractSnapshot,
    cfg: OptionsConfig,
) -> ContractFilterResult:
    right = required_right(signal.direction)
    if contract.right != right:
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="wrong_right")
    if contract.dte < int(cfg.allowed_dte_min):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="dte_too_short")
    if contract.dte > int(cfg.allowed_dte_max):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="dte_too_long")
    if contract.bid <= 0 or contract.ask <= 0:
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="invalid_bid_ask")
    if contract.ask < contract.bid:
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="crossed_market")
    if contract.mid <= 0:
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="invalid_mid")
    if contract.spread_pct > float(cfg.max_spread_pct):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="spread_too_wide")

    delta = contract.delta
    if delta is None:
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="missing_delta")
    delta_abs = abs(float(delta))
    if delta_abs < float(cfg.target_delta_min):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="delta_too_low")
    if delta_abs > float(cfg.target_delta_max):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="delta_too_high")

    if int(contract.open_interest or 0) < int(cfg.min_open_interest):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="open_interest_too_low")
    if int(contract.volume or 0) < int(cfg.min_contract_volume):
        return _result(signal=signal, contract=contract, cfg=cfg, passed=False, reject_reason="volume_too_low")

    return _result(signal=signal, contract=contract, cfg=cfg, passed=True, reject_reason=None)


def select_contract(
    signal: UnderlyingSignal,
    contracts: list[OptionContractSnapshot],
    cfg: OptionsConfig,
) -> tuple[list[ContractFilterResult], SelectedContract | None]:
    evaluated = [evaluate_contract(signal=signal, contract=c, cfg=cfg) for c in contracts]
    passed = [r for r in evaluated if r.passed]
    if not passed:
        return evaluated, None
    best = min(passed, key=lambda r: r.score)
    reason = (
        "closest_to_target_delta_with_best_liquidity_and_spread "
        f"(delta_pref={cfg.target_delta_preference:.2f}, max_spread_pct={cfg.max_spread_pct:.4f})"
    )
    return evaluated, SelectedContract(contract=best.contract, selection_reason=reason)
