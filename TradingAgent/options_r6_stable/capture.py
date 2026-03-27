from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config_loader import AppConfig
from .contract_selector import select_contract
from .models import ContractFilterResult, OptionContractSnapshot, PortfolioState, TradePlan, TradeRejection, UnderlyingSignal
from .strategy import build_trade_plan


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _stable_id(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1()
    h.update(prefix.encode("utf-8"))
    for part in parts:
        h.update(b"\x1f")
        h.update(_json_dumps(part).encode("utf-8"))
    return f"{prefix}_{h.hexdigest()[:20]}"


def _parse_ts(value: str, timezone_name: str) -> datetime:
    normalized = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return dt.astimezone(ZoneInfo(timezone_name))


def _now_iso(timezone_name: str) -> str:
    return datetime.now(tz=ZoneInfo(timezone_name)).isoformat()


def _session_date(value: str, timezone_name: str) -> str:
    return _parse_ts(value, timezone_name).date().isoformat()


def _combine_session_time(session_date: str, hhmm: str, timezone_name: str) -> str:
    return datetime.fromisoformat(f"{session_date}T{hhmm}:00").replace(tzinfo=ZoneInfo(timezone_name)).isoformat()


def _intrinsic_value(contract: OptionContractSnapshot, underlying_price: float | None) -> float | None:
    if underlying_price is None:
        return None
    if contract.right == "call":
        return max(float(underlying_price) - float(contract.strike), 0.0)
    return max(float(contract.strike) - float(underlying_price), 0.0)


def _upsert(conn: sqlite3.Connection, table: str, pk_col: str, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    update_cols = [c for c in columns if c != pk_col]
    update_sql = ", ".join([f"{c}=excluded.{c}" for c in update_cols])
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk_col}) DO UPDATE SET {update_sql}"
    )
    conn.execute(sql, tuple(row[c] for c in columns))


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, tuple(row[c] for c in columns))


def _score_value(result: ContractFilterResult) -> float:
    return float(result.score_details.get("composite_hint", float(result.score[0]) + float(result.score[1])))


def record_decision_capture(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    signal: UnderlyingSignal,
    chain: list[OptionContractSnapshot],
    account_equity: float,
    source_provider: str = "sample_csv",
    mode: str = "record_demo",
    run_id: str | None = None,
    portfolio_state: PortfolioState | None = None,
) -> dict[str, Any]:
    now_ts = _now_iso(cfg.timezone)
    session_date = _session_date(signal.event_ts, cfg.timezone)
    event_id = signal.event_id or _stable_id(
        "evt",
        signal.symbol,
        signal.event_ts,
        signal.direction,
        signal.variant_id,
        signal.ref_horizon,
        signal.include_today_or,
    )
    strategy_id = f"OPTIONS_R6_V1__{signal.variant_id}"
    scope_id = run_id or "default"

    evaluated, selected = select_contract(signal=signal, contracts=chain, cfg=cfg.options)
    plan = build_trade_plan(
        signal=signal,
        chain=chain,
        account_equity=account_equity,
        cfg=cfg.options,
        portfolio_state=portfolio_state,
    )
    passed = [result for result in evaluated if result.passed]
    batch_id = _stable_id("chainbatch", scope_id, event_id, source_provider, signal.event_ts, len(chain))

    signal_row = {
        "event_id": event_id,
        "symbol": signal.symbol,
        "session_date": session_date,
        "event_ts": signal.event_ts,
        "direction": signal.direction,
        "variant_id": signal.variant_id,
        "ref_horizon": signal.ref_horizon,
        "include_today_or": signal.include_today_or,
        "confidence": signal.confidence,
        "underlying_price": signal.underlying_price,
        "underlying_stop_price": signal.underlying_stop_price,
        "bar_open": signal.bar_open,
        "bar_high": signal.bar_high,
        "bar_low": signal.bar_low,
        "bar_close": signal.bar_close,
        "ema20": signal.ema20,
        "ema20_slope": signal.ema20_slope,
        "signal_age_seconds": 0.0,
        "signal_phase": "entry_signal",
        "eligibility_json": _json_dumps(
            {
                "mode": mode,
                "symbol_in_universe": True,
                "contracts_loaded": len(chain),
                "contracts_passing_filters": len(passed),
            }
        ),
        "raw_signal_json": _json_dumps(asdict(signal)),
        "data_json": _json_dumps({"mode": mode, "captured_at": now_ts}),
    }
    _upsert(conn, "underlying_r6_signals", "event_id", signal_row)

    _upsert(
        conn,
        "option_chain_batches",
        "batch_id",
        {
            "batch_id": batch_id,
            "run_id": run_id,
            "symbol": signal.symbol,
            "event_id": event_id,
            "session_date": session_date,
            "asof_ts": signal.event_ts,
            "source_provider": source_provider,
            "underlying_price": signal.underlying_price,
            "contracts_seen": len(chain),
            "contracts_eligible": len(passed),
            "notes_json": _json_dumps({"mode": mode}),
            "created_at": now_ts,
        },
    )

    snapshot_ids: dict[str, str] = {}
    for contract in chain:
        snapshot_id = _stable_id("snap", scope_id, batch_id, contract.option_symbol)
        snapshot_ids[contract.option_symbol] = snapshot_id
        intrinsic_value = _intrinsic_value(contract, signal.underlying_price)
        extrinsic_value = None if intrinsic_value is None else max(float(contract.mid) - intrinsic_value, 0.0)
        _upsert(
            conn,
            "option_chain_snapshots",
            "snapshot_id",
            {
                "snapshot_id": snapshot_id,
                "symbol": signal.symbol,
                "asof_ts": signal.event_ts,
                "option_symbol": contract.option_symbol,
                "right_side": contract.right,
                "expiration_date": contract.expiration_date,
                "strike": contract.strike,
                "dte": contract.dte,
                "bid": contract.bid,
                "ask": contract.ask,
                "mid": contract.mid,
                "last": contract.last,
                "delta": contract.delta,
                "gamma": contract.gamma,
                "theta": contract.theta,
                "vega": contract.vega,
                "iv": contract.iv,
                "open_interest": contract.open_interest,
                "volume": contract.volume,
                "source_provider": source_provider,
                "snapshot_batch_id": batch_id,
                "underlying_price": signal.underlying_price,
                "spread_pct": contract.spread_pct,
                "mark_price": contract.mid,
                "source_payload_json": _json_dumps({"mode": mode}),
                "quote_ts": signal.event_ts,
                "bid_size": None,
                "ask_size": None,
                "in_the_money": None if intrinsic_value is None else int(intrinsic_value > 0),
                "intrinsic_value": intrinsic_value,
                "extrinsic_value": extrinsic_value,
            },
        )

    for result in evaluated:
        candidate_id = _stable_id("cand", scope_id, event_id, result.contract.option_symbol)
        _upsert(
            conn,
            "option_contract_candidates",
            "candidate_id",
            {
                "candidate_id": candidate_id,
                "run_id": run_id,
                "event_id": event_id,
                "symbol": signal.symbol,
                "option_symbol": result.contract.option_symbol,
                "right_side": result.contract.right,
                "expiration_date": result.contract.expiration_date,
                "strike": result.contract.strike,
                "dte": result.contract.dte,
                "delta": result.contract.delta,
                "bid": result.contract.bid,
                "ask": result.contract.ask,
                "mid": result.contract.mid,
                "spread_pct": result.contract.spread_pct,
                "open_interest": result.contract.open_interest,
                "volume": result.contract.volume,
                "passed_filters": int(result.passed),
                "reject_reason": result.reject_reason,
                "rank_score": _json_dumps({"score_tuple": list(result.score), "score_details": result.score_details}),
                "snapshot_id": snapshot_ids[result.contract.option_symbol],
                "selection_context_json": _json_dumps(
                    {
                        "mode": mode,
                        "direction": signal.direction,
                        "target_delta_preference": cfg.options.target_delta_preference,
                        "selection_batch_id": batch_id,
                    }
                ),
                "filter_flags_json": _json_dumps(result.filter_flags),
                "decision_ts": now_ts,
            },
        )

    _insert(
        conn,
        "options_decision_steps",
        {
            "run_id": run_id,
            "ts": now_ts,
            "event_id": event_id,
            "position_id": None,
            "trade_id": None,
            "symbol": signal.symbol,
            "option_symbol": None,
            "stage": "signal_ingest",
            "decision": "accepted",
            "reason_code": "signal_received",
            "score": signal.confidence,
            "data_json": _json_dumps(asdict(signal)),
        },
    )
    _insert(
        conn,
        "options_decision_steps",
        {
            "run_id": run_id,
            "ts": now_ts,
            "event_id": event_id,
            "position_id": None,
            "trade_id": None,
            "symbol": signal.symbol,
            "option_symbol": None,
            "stage": "chain_snapshot",
            "decision": "captured",
            "reason_code": "chain_loaded",
            "score": float(len(chain)),
            "data_json": _json_dumps(
                {
                    "batch_id": batch_id,
                    "contracts_seen": len(chain),
                    "contracts_passing_filters": len(passed),
                    "source_provider": source_provider,
                }
            ),
        },
    )

    selection_id: str | None = None
    position_id: str | None = None
    if selected is not None:
        selected_result = next(result for result in passed if result.contract.option_symbol == selected.contract.option_symbol)
        sorted_passed = sorted(passed, key=lambda result: result.score)
        selection_rank = next(idx for idx, result in enumerate(sorted_passed, start=1) if result.contract.option_symbol == selected.contract.option_symbol)
        selection_id = _stable_id("sel", scope_id, event_id, selected.contract.option_symbol)
        _upsert(
            conn,
            "selected_option_contracts",
            "selection_id",
            {
                "selection_id": selection_id,
                "run_id": run_id,
                "event_id": event_id,
                "symbol": signal.symbol,
                "option_symbol": selected.contract.option_symbol,
                "right_side": selected.contract.right,
                "expiration_date": selected.contract.expiration_date,
                "strike": selected.contract.strike,
                "dte": selected.contract.dte,
                "delta": selected.contract.delta,
                "bid": selected.contract.bid,
                "ask": selected.contract.ask,
                "mid": selected.contract.mid,
                "spread_pct": selected.contract.spread_pct,
                "selection_reason": selected.selection_reason,
                "created_at": now_ts,
                "snapshot_id": snapshot_ids[selected.contract.option_symbol],
                "selection_rank": selection_rank,
                "selection_context_json": _json_dumps(
                    {
                        "score_tuple": list(selected_result.score),
                        "score_details": selected_result.score_details,
                        "filter_flags": selected_result.filter_flags,
                        "mode": mode,
                    }
                ),
                "selection_score": _score_value(selected_result),
                "selection_limit_price": selected.contract.mid,
            },
        )
        _insert(
            conn,
            "options_decision_steps",
            {
                "run_id": run_id,
                "ts": now_ts,
                "event_id": event_id,
                "position_id": None,
                "trade_id": None,
                "symbol": signal.symbol,
                "option_symbol": selected.contract.option_symbol,
                "stage": "contract_selection",
                "decision": "accepted",
                "reason_code": "contract_selected",
                "score": _score_value(selected_result),
                "data_json": _json_dumps(
                    {
                        "selection_id": selection_id,
                        "selection_rank": selection_rank,
                        "selection_reason": selected.selection_reason,
                    }
                ),
            },
        )
    else:
        _insert(
            conn,
            "options_decision_steps",
            {
                "run_id": run_id,
                "ts": now_ts,
                "event_id": event_id,
                "position_id": None,
                "trade_id": None,
                "symbol": signal.symbol,
                "option_symbol": None,
                "stage": "contract_selection",
                "decision": "rejected",
                "reason_code": "no_contract_passed_filters",
                "score": None,
                "data_json": _json_dumps({"contracts_seen": len(chain)}),
            },
        )

    accepted = isinstance(plan, TradePlan)
    if accepted:
        assert isinstance(plan, TradePlan)
        position_id = _stable_id("pos", scope_id, event_id, plan.contract.option_symbol, "planned")
        premium_hard_stop = None
        decision_context = {
            "mode": mode,
            "selection_reason": plan.selection_reason,
            "chain_batch_id": batch_id,
            "selected_contract": plan.contract.option_symbol,
            "contracts_loaded": len(chain),
            "contracts_passing_filters": len(passed),
        }
        risk_context = dict(plan.budget_context)
        risk_context.update(
            {
                "premium_per_contract": float(plan.premium_per_contract),
                "premium_at_risk_total": float(plan.premium_at_risk_total),
                "max_budget_dollars": float(plan.max_budget_dollars),
                "per_trade_budget_dollars": float(plan.per_trade_budget_dollars),
                "max_contracts_per_trade": int(cfg.options.max_contracts_per_trade),
            }
        )
        _upsert(
            conn,
            "options_positions",
            "position_id",
            {
                "position_id": position_id,
                "run_id": run_id,
                "symbol": signal.symbol,
                "option_symbol": plan.contract.option_symbol,
                "event_id": event_id,
                "strategy_id": strategy_id,
                "side": "buy",
                "contracts": plan.contracts,
                "entry_ts": signal.event_ts,
                "entry_limit": plan.contract.mid,
                "entry_fill": None,
                "premium_at_risk": plan.premium_at_risk_total,
                "underlying_entry_px": signal.underlying_price,
                "underlying_stop_px": signal.underlying_stop_price,
                "status": "PLANNED",
                "broker_order_id": None,
                "session_date": session_date,
                "created_at": now_ts,
                "updated_at": now_ts,
                "selection_id": selection_id,
                "entry_snapshot_id": snapshot_ids[plan.contract.option_symbol],
                "entry_bid": plan.contract.bid,
                "entry_ask": plan.contract.ask,
                "entry_mid": plan.contract.mid,
                "entry_delta": plan.contract.delta,
                "entry_iv": plan.contract.iv,
                "entry_spread_pct": plan.contract.spread_pct,
                "entry_open_interest": plan.contract.open_interest,
                "entry_volume": plan.contract.volume,
                "entry_slippage_estimate": plan.contract.spread / 2.0,
                "contract_multiplier": 100,
                "underlying_event_json": _json_dumps(asdict(signal)),
                "risk_context_json": _json_dumps(risk_context),
                "decision_context_json": _json_dumps(decision_context),
                "broker_position_json": None,
                "entry_quote_ts": signal.event_ts,
                "premium_hard_stop": premium_hard_stop,
                "time_exit_ts": _combine_session_time(session_date, cfg.options.force_exit_time, cfg.timezone),
                "max_contract_mid": plan.contract.mid,
                "min_contract_mid": plan.contract.mid,
                "max_underlying_price": signal.underlying_price,
                "min_underlying_price": signal.underlying_price,
                "last_mark_ts": signal.event_ts,
                "exit_plan_json": _json_dumps(
                    {
                        "same_day_exit_only": cfg.options.same_day_exit_only,
                        "force_exit_time": cfg.options.force_exit_time,
                        "premium_hard_stop": premium_hard_stop,
                    }
                ),
            },
        )
        _insert(
            conn,
            "options_decision_steps",
            {
                "run_id": run_id,
                "ts": now_ts,
                "event_id": event_id,
                "position_id": position_id,
                "trade_id": None,
                "symbol": signal.symbol,
                "option_symbol": plan.contract.option_symbol,
                "stage": "position_sizing",
                "decision": "accepted",
                "reason_code": "trade_plan_created",
                "score": float(plan.premium_at_risk_total),
                "data_json": _json_dumps(risk_context),
            },
        )
        _insert(
            conn,
            "options_portfolio_snapshots",
            {
                "run_id": run_id,
                "ts": now_ts,
                "session_date": session_date,
                "mode": mode,
                "cash": float(account_equity) - float(plan.premium_at_risk_total),
                "buying_power": float(account_equity) - float(plan.premium_at_risk_total),
                "portfolio_value": float(account_equity),
                "options_market_value": float(plan.premium_at_risk_total),
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "open_positions": 1,
                "open_orders": 0,
                "exposure_json": _json_dumps(
                    {
                        "symbol": signal.symbol,
                        "direction": signal.direction,
                        "contracts": plan.contracts,
                        "premium_at_risk_total": plan.premium_at_risk_total,
                    }
                ),
                "broker_account_json": _json_dumps({"mode": mode, "status": "planned_only"}),
            },
        )
        _insert(
            conn,
            "options_events",
            {
                "run_id": run_id,
                "ts": now_ts,
                "event_id": event_id,
                "symbol": signal.symbol,
                "level": "INFO",
                "event_type": "trade_planned",
                "session_date": session_date,
                "stage": "trade_plan",
                "position_id": position_id,
                "trade_id": None,
                "broker_order_id": None,
                "message": f"Planned options trade for {signal.symbol} using {plan.contract.option_symbol}",
                "data_json": _json_dumps(
                    {
                        "selection_id": selection_id,
                        "premium_at_risk_total": plan.premium_at_risk_total,
                        "contracts": plan.contracts,
                    }
                ),
            },
        )
    else:
        assert isinstance(plan, TradeRejection)
        miss_id = _stable_id("miss", scope_id, event_id, plan.reason)
        _upsert(
            conn,
            "options_missed_trades",
            "miss_id",
            {
                "miss_id": miss_id,
                "run_id": run_id,
                "event_id": event_id,
                "symbol": signal.symbol,
                "stage": "trade_plan",
                "reason": plan.reason,
                "selection_context_json": _json_dumps(
                    {
                        "contracts_loaded": len(chain),
                        "contracts_passing_filters": len(passed),
                        "selected_contract": None if selected is None else selected.contract.option_symbol,
                    }
                ),
                "data_json": _json_dumps(
                    {
                        "mode": mode,
                        "signal": asdict(signal),
                        "rejection_context": plan.context,
                    }
                ),
                "created_at": now_ts,
            },
        )
        _insert(
            conn,
            "options_decision_steps",
            {
                "run_id": run_id,
                "ts": now_ts,
                "event_id": event_id,
                "position_id": None,
                "trade_id": None,
                "symbol": signal.symbol,
                "option_symbol": None if selected is None else selected.contract.option_symbol,
                "stage": "position_sizing",
                "decision": "rejected",
                "reason_code": plan.reason,
                "score": None,
                "data_json": _json_dumps({"mode": mode}),
            },
        )
        _insert(
            conn,
            "options_events",
            {
                "run_id": run_id,
                "ts": now_ts,
                "event_id": event_id,
                "symbol": signal.symbol,
                "level": "WARNING",
                "event_type": "trade_skipped",
                "session_date": session_date,
                "stage": "trade_plan",
                "position_id": None,
                "trade_id": None,
                "broker_order_id": None,
                "message": f"Skipped options trade for {signal.symbol}: {plan.reason}",
                "data_json": _json_dumps(
                    {
                        "contracts_loaded": len(chain),
                        "contracts_passing_filters": len(passed),
                        "selected_contract": None if selected is None else selected.contract.option_symbol,
                        "rejection_context": plan.context,
                    }
                ),
            },
        )

    conn.commit()

    return {
        "mode": mode,
        "run_id": run_id,
        "event_id": event_id,
        "selection_id": selection_id,
        "position_id": position_id,
        "strategy_id": strategy_id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "accepted": accepted,
        "contracts_loaded": len(chain),
        "contracts_passing_filters": len(passed),
        "selected_contract": None if selected is None else selected.contract.option_symbol,
        "plan_type": type(plan).__name__,
        "plan_reason": plan.selection_reason if isinstance(plan, TradePlan) else plan.reason,
        "db_path": str(cfg.resolved_db_path),
    }
