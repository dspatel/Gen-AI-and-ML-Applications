from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config_loader import AppConfig, OptionsConfig
from .contract_selector import select_contract
from .models import OptionContractSnapshot, PortfolioState, UnderlyingSignal
from .research import _load_research_signals, _load_chain, _load_outcome, _parse_ts
from .strategy import build_trade_plan


@dataclass(frozen=True)
class ContractVariant:
    name: str
    dte_min: int
    dte_max: int
    delta_min: float
    delta_max: float
    delta_preference: float


@dataclass(frozen=True)
class ExitVariant:
    name: str
    mode: str
    tp_pct: float | None = None
    lock_levels: tuple[tuple[float, float], ...] = ()
    arm_pct: float | None = None
    min_floor_pct: float | None = None
    capture_pct: float | None = None
    max_floor_pct: float | None = None
    stall_arm_pct: float | None = None
    stall_bars: int | None = None
    use_underlying_progress: bool = False
    retrace_arm_pct: float | None = None
    retrace_pct: float | None = None
    decay_arm_pct: float | None = None
    decay_bars: int | None = None
    adverse_bars: int | None = None


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _load_option_path_rows(conn: sqlite3.Connection, event_id: str, option_symbol: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ts, open, high, low, close, volume
        FROM research_input_option_bar_paths
        WHERE event_id = ? AND option_symbol = ?
        ORDER BY ts
        """,
        (event_id, option_symbol),
    ).fetchall()
    return [
        {
            "ts": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0.0),
        }
        for row in rows
    ]


def _load_underlying_path_rows(conn: sqlite3.Connection, event_id: str, symbol: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ts, open, high, low, close, volume, timeframe_min
        FROM research_input_underlying_bar_paths
        WHERE event_id = ? AND symbol = ?
        ORDER BY ts
        """,
        (event_id, symbol),
    ).fetchall()
    return [
        {
            "ts": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0.0),
            "timeframe_min": int(row[6] or 1),
        }
        for row in rows
    ]


def _session_date(value: str) -> str:
    return _parse_ts(value, "America/Chicago").date().isoformat()


def _entry_ts(signal: UnderlyingSignal, path_rows: list[dict[str, Any]], timezone_name: str) -> str | None:
    event_dt = _parse_ts(signal.event_ts, timezone_name)
    max_dt = event_dt + timedelta(minutes=5)
    for row in path_rows:
        ts = _parse_ts(row["ts"], timezone_name)
        if ts >= event_dt and ts <= max_dt:
            return row["ts"]
    return None


def _favorable_underlying_extreme(direction: str, row: dict[str, Any]) -> float:
    if str(direction).upper() == "BEARISH":
        return -float(row["low"])
    return float(row["high"])


def _underlying_close_progress_value(direction: str, row: dict[str, Any]) -> float:
    if str(direction).upper() == "BEARISH":
        return -float(row["close"])
    return float(row["close"])


def _is_adverse_underlying_close(direction: str, current_close: float, previous_close: float) -> bool:
    if str(direction).upper() == "BEARISH":
        return current_close > previous_close
    return current_close < previous_close


def _next_bar_open_exit(
    rows: list[dict[str, Any]],
    idx: int,
    *,
    baseline_exit_ts: str,
    baseline_exit_fill: float,
    reason: str,
) -> dict[str, Any]:
    next_idx = idx + 1
    if next_idx < len(rows):
        return {
            "exit_ts": str(rows[next_idx]["ts"]),
            "exit_fill": float(rows[next_idx]["open"]),
            "exit_reason": reason,
        }
    return {
        "exit_ts": baseline_exit_ts,
        "exit_fill": baseline_exit_fill,
        "exit_reason": reason,
    }


def _simulate_exit(
    *,
    signal: UnderlyingSignal,
    outcome_row: sqlite3.Row,
    path_rows: list[dict[str, Any]],
    underlying_path_rows: list[dict[str, Any]],
    exit_variant: ExitVariant,
    timezone_name: str,
) -> dict[str, Any]:
    baseline_exit_ts = str(outcome_row["exit_ts"])
    baseline_exit_dt = _parse_ts(baseline_exit_ts, timezone_name)
    entry_fill = float(outcome_row["entry_fill"])
    baseline_exit_fill = float(outcome_row["exit_fill"])
    entry_ts = _entry_ts(signal, path_rows, timezone_name)
    if entry_ts is None:
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    entry_dt = _parse_ts(entry_ts, timezone_name)
    rows = [
        row
        for row in path_rows
        if _parse_ts(row["ts"], timezone_name) >= entry_dt and _parse_ts(row["ts"], timezone_name) <= baseline_exit_dt
    ]
    if not rows:
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    underlying_rows = [
        row
        for row in underlying_path_rows
        if _parse_ts(row["ts"], timezone_name) >= entry_dt and _parse_ts(row["ts"], timezone_name) <= baseline_exit_dt
    ]
    underlying_by_ts = {str(row["ts"]): row for row in underlying_rows}
    if exit_variant.mode == "baseline":
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    if exit_variant.mode == "take_profit":
        assert exit_variant.tp_pct is not None
        target_price = entry_fill * (1.0 + float(exit_variant.tp_pct))
        for row in rows:
            if float(row["high"]) >= target_price:
                return {
                    "exit_ts": str(row["ts"]),
                    "exit_fill": float(target_price),
                    "exit_reason": f"tp_{int(round(float(exit_variant.tp_pct) * 100))}pct",
                }
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    if exit_variant.mode == "lock":
        active_floor: float | None = None
        pending_floor: float | None = None
        for idx, row in enumerate(rows):
            if idx > 0 and pending_floor is not None:
                active_floor = max(active_floor or pending_floor, pending_floor)
                pending_floor = None
            if active_floor is not None and float(row["low"]) <= active_floor:
                return {
                    "exit_ts": str(row["ts"]),
                    "exit_fill": float(active_floor),
                    "exit_reason": f"{exit_variant.name}_floor_hit",
                }
            best_next_floor = pending_floor
            for trigger_pct, floor_pct in exit_variant.lock_levels:
                trigger_price = entry_fill * (1.0 + float(trigger_pct))
                floor_price = entry_fill * (1.0 + float(floor_pct))
                if float(row["high"]) >= trigger_price:
                    best_next_floor = floor_price if best_next_floor is None else max(best_next_floor, floor_price)
            pending_floor = best_next_floor
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    if exit_variant.mode == "ratchet":
        assert exit_variant.arm_pct is not None
        assert exit_variant.min_floor_pct is not None
        assert exit_variant.capture_pct is not None
        active_floor: float | None = None
        pending_floor: float | None = None
        peak_price = entry_fill
        for idx, row in enumerate(rows):
            if idx > 0 and pending_floor is not None:
                active_floor = max(active_floor or pending_floor, pending_floor)
                pending_floor = None
            if active_floor is not None and float(row["low"]) <= active_floor:
                return {
                    "exit_ts": str(row["ts"]),
                    "exit_fill": float(active_floor),
                    "exit_reason": f"{exit_variant.name}_floor_hit",
                }
            peak_price = max(peak_price, float(row["high"]))
            peak_return = (peak_price / entry_fill) - 1.0
            if peak_return >= float(exit_variant.arm_pct):
                floor_return = max(float(exit_variant.min_floor_pct), peak_return * float(exit_variant.capture_pct))
                if exit_variant.max_floor_pct is not None:
                    floor_return = min(floor_return, float(exit_variant.max_floor_pct))
                candidate_floor = entry_fill * (1.0 + floor_return)
                pending_floor = candidate_floor if pending_floor is None else max(pending_floor, candidate_floor)
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    if exit_variant.mode in {"stall", "lock_stall"}:
        assert exit_variant.stall_arm_pct is not None
        assert exit_variant.stall_bars is not None
        active_floor: float | None = None
        pending_floor: float | None = None
        armed = False
        last_progress_idx: int | None = None
        best_option_peak = entry_fill
        best_underlying_peak: float | None = None
        for idx, row in enumerate(rows):
            if exit_variant.mode == "lock_stall":
                if idx > 0 and pending_floor is not None:
                    active_floor = max(active_floor or pending_floor, pending_floor)
                    pending_floor = None
                if active_floor is not None and float(row["low"]) <= active_floor:
                    return {
                        "exit_ts": str(row["ts"]),
                        "exit_fill": float(active_floor),
                        "exit_reason": f"{exit_variant.name}_floor_hit",
                    }
                best_next_floor = pending_floor
                for trigger_pct, floor_pct in exit_variant.lock_levels:
                    trigger_price = entry_fill * (1.0 + float(trigger_pct))
                    floor_price = entry_fill * (1.0 + float(floor_pct))
                    if float(row["high"]) >= trigger_price:
                        best_next_floor = floor_price if best_next_floor is None else max(best_next_floor, floor_price)
                pending_floor = best_next_floor

            progress = False
            if float(row["high"]) > best_option_peak:
                best_option_peak = float(row["high"])
                if armed:
                    progress = True
            if not armed and float(row["high"]) >= entry_fill * (1.0 + float(exit_variant.stall_arm_pct)):
                armed = True
                last_progress_idx = idx
            if armed and exit_variant.use_underlying_progress:
                underlying_row = underlying_by_ts.get(str(row["ts"]))
                if underlying_row is not None:
                    favorable = _favorable_underlying_extreme(signal.direction, underlying_row)
                    if best_underlying_peak is None or favorable > best_underlying_peak:
                        if best_underlying_peak is not None:
                            progress = True
                        best_underlying_peak = favorable
            if armed and progress:
                last_progress_idx = idx
            if armed and last_progress_idx is not None and (idx - last_progress_idx) >= int(exit_variant.stall_bars):
                return _next_bar_open_exit(
                    rows,
                    idx,
                    baseline_exit_ts=baseline_exit_ts,
                    baseline_exit_fill=baseline_exit_fill,
                    reason=f"{exit_variant.name}_stall_exit",
                )
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    if exit_variant.mode == "retrace":
        assert exit_variant.retrace_arm_pct is not None
        assert exit_variant.retrace_pct is not None
        armed = False
        peak_high = entry_fill
        for idx, row in enumerate(rows):
            peak_high = max(peak_high, float(row["high"]))
            peak_return = (peak_high / entry_fill) - 1.0
            if not armed and peak_return >= float(exit_variant.retrace_arm_pct):
                armed = True
            if not armed:
                continue
            protected_return = max(0.0, peak_return * (1.0 - float(exit_variant.retrace_pct)))
            threshold_price = entry_fill * (1.0 + protected_return)
            if float(row["close"]) <= threshold_price:
                return _next_bar_open_exit(
                    rows,
                    idx,
                    baseline_exit_ts=baseline_exit_ts,
                    baseline_exit_fill=baseline_exit_fill,
                    reason=f"{exit_variant.name}_retrace_exit",
                )
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    if exit_variant.mode in {"decay", "lock_decay"}:
        assert exit_variant.decay_arm_pct is not None
        assert exit_variant.decay_bars is not None
        assert exit_variant.adverse_bars is not None
        active_floor: float | None = None
        pending_floor: float | None = None
        armed = False
        bars_since_progress = 0
        adverse_count = 0
        best_underlying_close_progress: float | None = None
        prev_underlying_close: float | None = None
        for idx, row in enumerate(rows):
            if exit_variant.mode == "lock_decay":
                if idx > 0 and pending_floor is not None:
                    active_floor = max(active_floor or pending_floor, pending_floor)
                    pending_floor = None
                if active_floor is not None and float(row["low"]) <= active_floor:
                    return {
                        "exit_ts": str(row["ts"]),
                        "exit_fill": float(active_floor),
                        "exit_reason": f"{exit_variant.name}_floor_hit",
                    }
                best_next_floor = pending_floor
                for trigger_pct, floor_pct in exit_variant.lock_levels:
                    trigger_price = entry_fill * (1.0 + float(trigger_pct))
                    floor_price = entry_fill * (1.0 + float(floor_pct))
                    if float(row["high"]) >= trigger_price:
                        best_next_floor = floor_price if best_next_floor is None else max(best_next_floor, floor_price)
                pending_floor = best_next_floor

            if not armed and float(row["high"]) >= entry_fill * (1.0 + float(exit_variant.decay_arm_pct)):
                armed = True
                bars_since_progress = 0
                adverse_count = 0
                underlying_row = underlying_by_ts.get(str(row["ts"]))
                if underlying_row is not None:
                    best_underlying_close_progress = _underlying_close_progress_value(signal.direction, underlying_row)
                    prev_underlying_close = float(underlying_row["close"])
                continue
            if not armed:
                continue

            underlying_row = underlying_by_ts.get(str(row["ts"]))
            if underlying_row is None:
                bars_since_progress += 1
            else:
                progress_value = _underlying_close_progress_value(signal.direction, underlying_row)
                if best_underlying_close_progress is None or progress_value > best_underlying_close_progress:
                    best_underlying_close_progress = progress_value
                    bars_since_progress = 0
                else:
                    bars_since_progress += 1
                current_close = float(underlying_row["close"])
                if prev_underlying_close is not None and _is_adverse_underlying_close(signal.direction, current_close, prev_underlying_close):
                    adverse_count += 1
                else:
                    adverse_count = 0
                prev_underlying_close = current_close

            if bars_since_progress >= int(exit_variant.decay_bars) and adverse_count >= int(exit_variant.adverse_bars):
                return _next_bar_open_exit(
                    rows,
                    idx,
                    baseline_exit_ts=baseline_exit_ts,
                    baseline_exit_fill=baseline_exit_fill,
                    reason=f"{exit_variant.name}_decay_exit",
                )
        return {
            "exit_ts": baseline_exit_ts,
            "exit_fill": baseline_exit_fill,
            "exit_reason": str(outcome_row["exit_reason"]),
        }
    raise ValueError(f"Unsupported exit mode: {exit_variant.mode}")


def _contract_cfg(base_cfg: OptionsConfig, variant: ContractVariant) -> OptionsConfig:
    return replace(
        base_cfg,
        allowed_dte_min=int(variant.dte_min),
        allowed_dte_max=int(variant.dte_max),
        target_delta_min=float(variant.delta_min),
        target_delta_max=float(variant.delta_max),
        target_delta_preference=float(variant.delta_preference),
    )


def _default_contract_variants() -> list[ContractVariant]:
    return [
        ContractVariant("cv_5_14_d30_45", 5, 14, 0.30, 0.45, 0.38),
        ContractVariant("cv_5_12_d30_45", 5, 12, 0.30, 0.45, 0.38),
        ContractVariant("cv_5_14_d25_40", 5, 14, 0.25, 0.40, 0.33),
        ContractVariant("cv_7_14_d25_40", 7, 14, 0.25, 0.40, 0.33),
        ContractVariant("cv_7_14_d35_50", 7, 14, 0.35, 0.50, 0.42),
        ContractVariant("cv_5_10_d20_35", 5, 10, 0.20, 0.35, 0.28),
        ContractVariant("cv_5_10_d40_60", 5, 10, 0.40, 0.60, 0.50),
        ContractVariant("cv_14_21_d40_60", 14, 21, 0.40, 0.60, 0.50),
    ]


def _default_exit_variants() -> list[ExitVariant]:
    return [
        ExitVariant("baseline", "baseline"),
        ExitVariant("tp_2pct", "take_profit", tp_pct=0.02),
        ExitVariant("tp_3pct", "take_profit", tp_pct=0.03),
        ExitVariant("tp_5pct", "take_profit", tp_pct=0.05),
        ExitVariant("lock_0p5_after_2", "lock", lock_levels=((0.02, 0.005),)),
        ExitVariant("lock_be_after_3", "lock", lock_levels=((0.03, 0.0),)),
        ExitVariant("lock_1_after_3", "lock", lock_levels=((0.03, 0.01),)),
        ExitVariant("lock_1_after_2", "lock", lock_levels=((0.02, 0.01),)),
        ExitVariant("lock_1p5_after_4", "lock", lock_levels=((0.04, 0.015),)),
        ExitVariant("adaptive_3_1_6_3", "lock", lock_levels=((0.03, 0.01), (0.06, 0.03))),
        ExitVariant("adaptive_2_0p5_4_1_6_2", "lock", lock_levels=((0.02, 0.005), (0.04, 0.01), (0.06, 0.02))),
        ExitVariant("adaptive_3_1_5_2_8_4", "lock", lock_levels=((0.03, 0.01), (0.05, 0.02), (0.08, 0.04))),
        ExitVariant("adaptive_5_2_10_5", "lock", lock_levels=((0.05, 0.02), (0.10, 0.05))),
        ExitVariant("ratchet_50_after_3_min1", "ratchet", arm_pct=0.03, min_floor_pct=0.01, capture_pct=0.50),
        ExitVariant("ratchet_40_after_4_min1", "ratchet", arm_pct=0.04, min_floor_pct=0.01, capture_pct=0.40),
        ExitVariant("ratchet_50_after_5_min2", "ratchet", arm_pct=0.05, min_floor_pct=0.02, capture_pct=0.50),
        ExitVariant("ratchet_60_after_4_min1_cap5", "ratchet", arm_pct=0.04, min_floor_pct=0.01, capture_pct=0.60, max_floor_pct=0.05),
        ExitVariant("retrace50_after3", "retrace", retrace_arm_pct=0.03, retrace_pct=0.50),
        ExitVariant("retrace40_after4", "retrace", retrace_arm_pct=0.04, retrace_pct=0.40),
        ExitVariant("retrace33_after5", "retrace", retrace_arm_pct=0.05, retrace_pct=0.33),
        ExitVariant("stall_3_for_5m", "stall", stall_arm_pct=0.03, stall_bars=5),
        ExitVariant("stall_3_for_5m_u", "stall", stall_arm_pct=0.03, stall_bars=5, use_underlying_progress=True),
        ExitVariant("stall_5_for_8m_u", "stall", stall_arm_pct=0.05, stall_bars=8, use_underlying_progress=True),
        ExitVariant("decay_3bars_2adv_after3", "decay", decay_arm_pct=0.03, decay_bars=3, adverse_bars=2),
        ExitVariant("decay_4bars_2adv_after3", "decay", decay_arm_pct=0.03, decay_bars=4, adverse_bars=2),
        ExitVariant("decay_5bars_3adv_after5", "decay", decay_arm_pct=0.05, decay_bars=5, adverse_bars=3),
        ExitVariant(
            "lock1_after3_stall5_u",
            "lock_stall",
            lock_levels=((0.03, 0.01),),
            stall_arm_pct=0.03,
            stall_bars=5,
            use_underlying_progress=True,
        ),
        ExitVariant(
            "lock1_after3_stall8_u",
            "lock_stall",
            lock_levels=((0.03, 0.01),),
            stall_arm_pct=0.03,
            stall_bars=8,
            use_underlying_progress=True,
        ),
        ExitVariant(
            "lock0p5_after2_stall5_u",
            "lock_stall",
            lock_levels=((0.02, 0.005),),
            stall_arm_pct=0.02,
            stall_bars=5,
            use_underlying_progress=True,
        ),
        ExitVariant(
            "lock1_after3_decay3_2",
            "lock_decay",
            lock_levels=((0.03, 0.01),),
            decay_arm_pct=0.03,
            decay_bars=3,
            adverse_bars=2,
        ),
        ExitVariant(
            "lock1_after3_decay4_2",
            "lock_decay",
            lock_levels=((0.03, 0.01),),
            decay_arm_pct=0.03,
            decay_bars=4,
            adverse_bars=2,
        ),
    ]


def _aggregate_results(trades: list[dict[str, Any]]) -> dict[str, Any]:
    gross_profit = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    gross_loss = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    active_days = sorted({str(t["session_date"]) for t in trades})
    net_pnl = round(sum(t["net_pnl"] for t in trades), 2)
    return {
        "trades": len(trades),
        "net_pnl": net_pnl,
        "win_rate": 0.0 if not trades else round(sum(1 for t in trades if t["net_pnl"] > 0) / len(trades), 4),
        "profit_factor": None if gross_loss == 0 else round(gross_profit / gross_loss, 4),
        "avg_trade_return_pct": 0.0 if not trades else round(sum(t["return_pct"] for t in trades) / len(trades), 6),
        "active_days": len(active_days),
        "avg_pnl_per_active_day": 0.0 if not active_days else round(net_pnl / len(active_days), 2),
    }


def _variant_trades(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    signals: list[UnderlyingSignal],
    contract_variant: ContractVariant,
    exit_variant: ExitVariant,
    starting_equity: float,
) -> list[dict[str, Any]]:
    variant_cfg = _contract_cfg(cfg.options, contract_variant)
    active_positions: list[dict[str, Any]] = []
    current_equity = float(starting_equity)
    daily_realized_pnl: dict[str, float] = defaultdict(float)
    daily_new_trades: dict[str, int] = defaultdict(int)
    trades: list[dict[str, Any]] = []

    def settle(asof_dt: datetime) -> None:
        nonlocal current_equity, active_positions
        remaining: list[dict[str, Any]] = []
        for pending in active_positions:
            if pending["exit_dt"] <= asof_dt:
                current_equity += float(pending["net_pnl"])
                daily_realized_pnl[pending["exit_session_date"]] += float(pending["net_pnl"])
            else:
                remaining.append(pending)
        active_positions = remaining

    for signal in signals:
        event_dt = _parse_ts(signal.event_ts, cfg.timezone)
        settle(event_dt)
        session_date = event_dt.date().isoformat()
        chain = _load_chain(conn, signal.event_id or "")
        portfolio_state = PortfolioState(
            cash_available=max(0.0, current_equity - sum(p["premium_at_risk"] for p in active_positions)),
            open_premium_total=sum(p["premium_at_risk"] for p in active_positions),
            open_symbol_premium=sum(p["premium_at_risk"] for p in active_positions if p["symbol"] == signal.symbol),
            open_direction_premium=sum(p["premium_at_risk"] for p in active_positions if p["direction"] == signal.direction),
            realized_pnl_today=daily_realized_pnl[session_date],
            new_trades_today=daily_new_trades[session_date],
        )
        plan = build_trade_plan(
            signal=signal,
            chain=chain,
            account_equity=current_equity,
            cfg=variant_cfg,
            portfolio_state=portfolio_state,
        )
        if not hasattr(plan, "contract"):
            continue
        outcome_row = _load_outcome(conn, signal.event_id or "", plan.contract.option_symbol)
        if outcome_row is None:
            continue
        path_rows = _load_option_path_rows(conn, signal.event_id or "", plan.contract.option_symbol)
        underlying_path_rows = _load_underlying_path_rows(conn, signal.event_id or "", signal.symbol)
        simulated = _simulate_exit(
            signal=signal,
            outcome_row=outcome_row,
            path_rows=path_rows,
            underlying_path_rows=underlying_path_rows,
            exit_variant=exit_variant,
            timezone_name=cfg.timezone,
        )
        entry_fill = float(outcome_row["entry_fill"])
        exit_fill = float(simulated["exit_fill"])
        net_pnl = (exit_fill - entry_fill) * int(plan.contracts) * 100.0
        exit_dt = _parse_ts(str(simulated["exit_ts"]), cfg.timezone)
        trades.append(
            {
                "session_date": session_date,
                "symbol": signal.symbol,
                "option_symbol": plan.contract.option_symbol,
                "direction": signal.direction,
                "premium_at_risk": float(plan.premium_at_risk_total),
                "net_pnl": float(net_pnl),
                "return_pct": 0.0 if float(plan.premium_at_risk_total) <= 0 else float(net_pnl) / float(plan.premium_at_risk_total),
                "exit_reason": str(simulated["exit_reason"]),
            }
        )
        active_positions.append(
            {
                "symbol": signal.symbol,
                "direction": signal.direction,
                "premium_at_risk": float(plan.premium_at_risk_total),
                "net_pnl": float(net_pnl),
                "exit_dt": exit_dt,
                "exit_session_date": exit_dt.date().isoformat(),
            }
        )
        daily_new_trades[session_date] += 1

    if active_positions:
        settle(max(p["exit_dt"] for p in active_positions))
    return trades


def _in_window(session_date: str, start: str | None, end: str | None) -> bool:
    if start and session_date < start:
        return False
    if end and session_date > end:
        return False
    return True


def _split_results(trades: list[dict[str, Any]], cfg: AppConfig, *, reveal_blind: bool) -> dict[str, dict[str, Any] | None]:
    train_trades = [
        trade
        for trade in trades
        if _in_window(str(trade["session_date"]), cfg.research.train.start, cfg.research.train.end)
    ]
    validation_trades = [
        trade
        for trade in trades
        if _in_window(str(trade["session_date"]), cfg.research.validation.start, cfg.research.validation.end)
    ]
    blind_trades = [
        trade
        for trade in trades
        if _in_window(str(trade["session_date"]), cfg.research.blind.start, cfg.research.blind.end)
    ]
    return {
        "all": _aggregate_results(trades),
        "train": _aggregate_results(train_trades),
        "validation": _aggregate_results(validation_trades),
        "blind": (_aggregate_results(blind_trades) if reveal_blind else None),
    }


def _selection_tuple(result: dict[str, Any], selection_policy: str) -> tuple[float, float, float, float]:
    validation = result["validation"] or {}
    train = result["train"] or {}
    combined = result["all"] or {}
    if selection_policy == "train_then_validation":
        primary = float(train.get("net_pnl", 0.0))
        secondary = float(validation.get("net_pnl", 0.0))
        tertiary = float(train.get("profit_factor") or -999.0)
        quaternary = float(validation.get("profit_factor") or -999.0)
        return (primary, secondary, tertiary, quaternary)
    primary = float(validation.get("net_pnl", 0.0))
    secondary = float(train.get("net_pnl", 0.0))
    tertiary = float(validation.get("profit_factor") or -999.0)
    quaternary = float(combined.get("profit_factor") or -999.0)
    return (primary, secondary, tertiary, quaternary)


def _passes_stability_gates(result: dict[str, Any], cfg: AppConfig) -> tuple[bool, list[str]]:
    gates = cfg.research.stability_gates
    train = result["train"] or {}
    validation = result["validation"] or {}
    reasons: list[str] = []

    if int(train.get("trades", 0)) < int(gates.min_train_trades):
        reasons.append("train_trades_below_min")
    if int(validation.get("trades", 0)) < int(gates.min_validation_trades):
        reasons.append("validation_trades_below_min")
    if int(train.get("active_days", 0)) < int(gates.min_train_active_days):
        reasons.append("train_active_days_below_min")
    if int(validation.get("active_days", 0)) < int(gates.min_validation_active_days):
        reasons.append("validation_active_days_below_min")

    train_pf = train.get("profit_factor")
    validation_pf = validation.get("profit_factor")
    if float(train_pf or 0.0) < float(gates.min_train_profit_factor):
        reasons.append("train_profit_factor_below_min")
    if validation_pf is not None and float(validation_pf or 0.0) < float(gates.min_validation_profit_factor):
        reasons.append("validation_profit_factor_below_min")

    if bool(gates.require_positive_train_pnl) and float(train.get("net_pnl", 0.0)) <= 0.0:
        reasons.append("train_net_pnl_not_positive")
    if bool(gates.require_positive_validation_pnl) and float(validation.get("net_pnl", 0.0)) <= 0.0:
        reasons.append("validation_net_pnl_not_positive")

    return (len(reasons) == 0, reasons)


def _stable_id(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1()
    h.update(prefix.encode("utf-8"))
    for part in parts:
        h.update(b"\x1f")
        h.update(_json_dumps(part).encode("utf-8"))
    return f"{prefix}_{h.hexdigest()[:20]}"


def _persist_protocol_results(
    conn: sqlite3.Connection,
    *,
    protocol_run_id: str,
    label: str,
    cfg: AppConfig,
    start: str,
    end: str,
    starting_equity: float,
    reveal_blind: bool,
    output_path: str,
    all_results: list[dict[str, Any]],
) -> None:
    created_at = datetime.now().isoformat()
    winner = all_results[0] if all_results else {}
    conn.execute("DELETE FROM options_protocol_results WHERE protocol_run_id = ?", (protocol_run_id,))
    conn.execute("DELETE FROM options_protocol_runs WHERE protocol_run_id = ?", (protocol_run_id,))
    conn.execute(
        """
        INSERT INTO options_protocol_runs (
            protocol_run_id, label, created_at, start, end, starting_equity, reveal_blind,
            selection_policy, train_start, train_end, validation_start, validation_end,
            blind_start, blind_end, configured_provider, total_variants,
            winning_contract_variant, winning_exit_variant, output_path, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            protocol_run_id,
            label,
            created_at,
            start,
            end,
            float(starting_equity),
            1 if reveal_blind else 0,
            cfg.research.selection_policy,
            cfg.research.train.start,
            cfg.research.train.end,
            cfg.research.validation.start,
            cfg.research.validation.end,
            cfg.research.blind.start,
            cfg.research.blind.end,
            cfg.market_data.historical_provider,
            len(all_results),
            winner.get("contract_variant"),
            winner.get("exit_variant"),
            output_path,
            _json_dumps(
                {
                    "top_contract_variant": winner.get("contract_variant"),
                    "top_exit_variant": winner.get("exit_variant"),
                    "reveal_blind": reveal_blind,
                }
            ),
        ),
    )
    for rank, row in enumerate(all_results, start=1):
        conn.execute(
            """
            INSERT INTO options_protocol_results (
                protocol_run_id, rank, contract_variant, exit_variant, selection_score,
                train_json, validation_json, blind_json, all_json, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                protocol_run_id,
                rank,
                row["contract_variant"],
                row["exit_variant"],
                float(row["selection_score"]),
                _json_dumps(row["train"]),
                _json_dumps(row["validation"]),
                (None if row["blind"] is None else _json_dumps(row["blind"])),
                _json_dumps(row["all"]),
                _json_dumps(
                    {
                        "selection_tuple": row["selection_tuple"],
                        "passes_stability_gates": row.get("passes_stability_gates"),
                        "stability_gate_reasons": row.get("stability_gate_reasons") or [],
                    }
                ),
            ),
        )
    conn.commit()


def run_exit_experiments(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    start: str,
    end: str,
    output_label: str,
    contract_variants: list[ContractVariant] | None = None,
    exit_variants: list[ExitVariant] | None = None,
) -> dict[str, Any]:
    contract_variants = contract_variants or _default_contract_variants()
    exit_variants = exit_variants or _default_exit_variants()
    signals = _load_research_signals(conn, start=start, end=end)
    out_dir = cfg.resolved_research_output_dir / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict[str, Any]] = []
    for contract_variant in contract_variants:
        for exit_variant in exit_variants:
            trades = _variant_trades(
                conn,
                cfg,
                signals=signals,
                contract_variant=contract_variant,
                exit_variant=exit_variant,
                starting_equity=100000.0,
            )
            combined = _aggregate_results(trades)
            train_summary = _aggregate_results(
                [
                    trade
                    for trade in trades
                    if _in_window(str(trade["session_date"]), cfg.research.train.start, cfg.research.train.end)
                ]
            )
            validation_summary = _aggregate_results(
                [
                    trade
                    for trade in trades
                    if _in_window(str(trade["session_date"]), cfg.research.validation.start, cfg.research.validation.end)
                ]
            )
            result = {
                "contract_variant": contract_variant.name,
                "exit_variant": exit_variant.name,
                "combined": combined,
                "train": train_summary,
                "validation": validation_summary,
            }
            all_results.append(result)

    all_results.sort(
        key=lambda row: (
            row["validation"]["net_pnl"],
            row["combined"]["net_pnl"],
            row["validation"]["profit_factor"] or -999.0,
            row["combined"]["profit_factor"] or -999.0,
        ),
        reverse=True,
    )
    output = {
        "label": output_label,
        "start": start,
        "end": end,
        "results": all_results,
        "top10": all_results[:10],
    }
    output_path = out_dir / f"{output_label}.json"
    output_path.write_text(_json_dumps(output), encoding="utf-8")
    return {
        "label": output_label,
        "start": start,
        "end": end,
        "results_count": len(all_results),
        "output_path": str(output_path),
        "top10": all_results[:10],
    }


def run_protocol_sweep(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    *,
    start: str,
    end: str,
    output_label: str,
    starting_equity: float = 100000.0,
    reveal_blind: bool = False,
    contract_variants: list[ContractVariant] | None = None,
    exit_variants: list[ExitVariant] | None = None,
) -> dict[str, Any]:
    contract_variants = contract_variants or _default_contract_variants()
    exit_variants = exit_variants or _default_exit_variants()
    signals = _load_research_signals(conn, start=start, end=end)
    out_dir = cfg.resolved_research_output_dir / "protocol_sweeps"
    out_dir.mkdir(parents=True, exist_ok=True)

    blind_hidden = bool(cfg.research.blind_test_locked and not reveal_blind)
    all_results: list[dict[str, Any]] = []
    for contract_variant in contract_variants:
        for exit_variant in exit_variants:
            trades = _variant_trades(
                conn,
                cfg,
                signals=signals,
                contract_variant=contract_variant,
                exit_variant=exit_variant,
                starting_equity=float(starting_equity),
            )
            summaries = _split_results(trades, cfg, reveal_blind=not blind_hidden)
            selection_tuple = _selection_tuple(summaries, cfg.research.selection_policy)
            all_results.append(
                {
                    "contract_variant": contract_variant.name,
                    "exit_variant": exit_variant.name,
                    "all": summaries["all"],
                    "train": summaries["train"],
                    "validation": summaries["validation"],
                    "blind": summaries["blind"],
                    "selection_tuple": selection_tuple,
                    "selection_score": float(selection_tuple[0]),
                }
            )

    for row in all_results:
        passes, reasons = _passes_stability_gates(row, cfg)
        row["passes_stability_gates"] = passes
        row["stability_gate_reasons"] = reasons
        if not passes:
            row["selection_tuple"] = (-10**12, -10**12, -10**12, -10**12)
            row["selection_score"] = float(-10**12)

    all_results.sort(
        key=lambda row: (
            1 if row.get("passes_stability_gates") else 0,
            row["selection_tuple"],
        ),
        reverse=True,
    )
    protocol_run_id = _stable_id(
        "protocol",
        output_label,
        start,
        end,
        starting_equity,
        reveal_blind,
        cfg.research.selection_policy,
    )
    winner = all_results[0] if all_results else None
    output = {
        "protocol_run_id": protocol_run_id,
        "label": output_label,
        "start": start,
        "end": end,
        "starting_equity": float(starting_equity),
        "selection_policy": cfg.research.selection_policy,
        "blind_hidden": blind_hidden,
        "splits": {
            "train": {"start": cfg.research.train.start, "end": cfg.research.train.end},
            "validation": {"start": cfg.research.validation.start, "end": cfg.research.validation.end},
            "blind": {"start": cfg.research.blind.start, "end": cfg.research.blind.end},
        },
        "winner": winner,
        "results_count": len(all_results),
        "top10": all_results[:10],
        "results": all_results,
    }
    output_path = out_dir / f"{output_label}.json"
    output_path.write_text(_json_dumps(output), encoding="utf-8")
    _persist_protocol_results(
        conn,
        protocol_run_id=protocol_run_id,
        label=output_label,
        cfg=cfg,
        start=start,
        end=end,
        starting_equity=float(starting_equity),
        reveal_blind=not blind_hidden,
        output_path=str(output_path),
        all_results=all_results,
    )
    return {
        "protocol_run_id": protocol_run_id,
        "label": output_label,
        "start": start,
        "end": end,
        "selection_policy": cfg.research.selection_policy,
        "blind_hidden": blind_hidden,
        "results_count": len(all_results),
        "winner": winner,
        "output_path": str(output_path),
        "top10": all_results[:10],
    }
