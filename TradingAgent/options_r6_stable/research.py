from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .capture import record_decision_capture
from .config_loader import AppConfig
from .models import OptionContractSnapshot, PortfolioState, UnderlyingSignal
from .sample_data import SAMPLE_RESEARCH_CHAIN_SNAPSHOTS, SAMPLE_RESEARCH_OUTCOMES, SAMPLE_RESEARCH_SIGNALS


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _stable_id(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1()
    h.update(prefix.encode("utf-8"))
    for part in parts:
        h.update(b"\x1f")
        h.update(_json_dumps(part).encode("utf-8"))
    return f"{prefix}_{h.hexdigest()[:20]}"


def _now_iso(timezone_name: str) -> str:
    return datetime.now(tz=ZoneInfo(timezone_name)).isoformat()


def _parse_ts(value: str, timezone_name: str) -> datetime:
    normalized = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
    return dt.astimezone(ZoneInfo(timezone_name))


def _insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    columns = list(row.keys())
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, tuple(row[c] for c in columns))


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


def seed_sample_research_inputs(conn: sqlite3.Connection, cfg: AppConfig) -> dict[str, Any]:
    now_ts = _now_iso(cfg.timezone)
    sample_event_ids = [row["event_id"] for row in SAMPLE_RESEARCH_SIGNALS]
    for event_id in sample_event_ids:
        conn.execute("DELETE FROM research_input_chain_snapshots WHERE event_id = ?", (event_id,))
    for signal in SAMPLE_RESEARCH_SIGNALS:
        row = dict(signal)
        row["created_at"] = now_ts
        _upsert(conn, "research_input_signals", "event_id", row)
    for chain_row in SAMPLE_RESEARCH_CHAIN_SNAPSHOTS:
        _insert(conn, "research_input_chain_snapshots", dict(chain_row))
    for outcome in SAMPLE_RESEARCH_OUTCOMES:
        row = dict(outcome)
        row["created_at"] = now_ts
        _upsert(conn, "research_input_outcomes", "outcome_id", row)
    conn.commit()
    return {
        "signals_seeded": len(SAMPLE_RESEARCH_SIGNALS),
        "chain_rows_seeded": len(SAMPLE_RESEARCH_CHAIN_SNAPSHOTS),
        "outcomes_seeded": len(SAMPLE_RESEARCH_OUTCOMES),
    }


def _load_research_signals(conn: sqlite3.Connection, start: str | None, end: str | None) -> list[UnderlyingSignal]:
    sql = [
        "SELECT event_id, symbol, direction, event_ts, variant_id, confidence, ref_horizon, include_today_or, "
        "underlying_price, underlying_stop_price, bar_open, bar_high, bar_low, bar_close, ema20, ema20_slope, "
        "source_tag, notes_json FROM research_input_signals WHERE 1=1"
    ]
    params: list[Any] = []
    if start:
        sql.append("AND session_date >= ?")
        params.append(start)
    if end:
        sql.append("AND session_date <= ?")
        params.append(end)
    sql.append("ORDER BY event_ts ASC")
    rows = conn.execute(" ".join(sql), params).fetchall()
    return [
        UnderlyingSignal(
            event_id=row[0],
            symbol=row[1],
            direction=row[2],
            event_ts=row[3],
            variant_id=row[4],
            confidence=row[5],
            ref_horizon=row[6],
            include_today_or=row[7],
            underlying_price=row[8],
            underlying_stop_price=row[9],
            bar_open=row[10],
            bar_high=row[11],
            bar_low=row[12],
            bar_close=row[13],
            ema20=row[14],
            ema20_slope=row[15],
            source_tag=row[16],
            notes_json=row[17],
        )
        for row in rows
    ]


def _load_chain(conn: sqlite3.Connection, event_id: str) -> list[OptionContractSnapshot]:
    rows = conn.execute(
        """
        SELECT option_symbol, symbol, right_side, expiration_date, strike, dte, bid, ask,
               delta, open_interest, volume, last, iv, gamma, theta, vega
        FROM research_input_chain_snapshots
        WHERE event_id = ?
        ORDER BY option_symbol
        """,
        (event_id,),
    ).fetchall()
    return [
        OptionContractSnapshot(
            option_symbol=row[0],
            underlying_symbol=row[1],
            right=row[2],
            expiration_date=row[3],
            strike=float(row[4]),
            dte=int(row[5]),
            bid=float(row[6]),
            ask=float(row[7]),
            delta=(None if row[8] is None else float(row[8])),
            open_interest=(None if row[9] is None else int(row[9])),
            volume=(None if row[10] is None else int(row[10])),
            last=(None if row[11] is None else float(row[11])),
            iv=(None if row[12] is None else float(row[12])),
            gamma=(None if row[13] is None else float(row[13])),
            theta=(None if row[14] is None else float(row[14])),
            vega=(None if row[15] is None else float(row[15])),
        )
        for row in rows
    ]


def _load_outcome(conn: sqlite3.Connection, event_id: str, option_symbol: str) -> sqlite3.Row | None:
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM research_input_outcomes WHERE event_id = ? AND option_symbol = ?",
        (event_id, option_symbol),
    ).fetchone()
    conn.row_factory = previous_factory
    return row


def _mid_from_bid_ask(bid: float | None, ask: float | None, fallback: float | None = None) -> float | None:
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (float(bid) + float(ask)) / 2.0
    return fallback

def _insert_position_marks(
    conn: sqlite3.Connection,
    run_id: str,
    position_id: str,
    entry_ts: str,
    exit_ts: str,
    underlying_entry_px: float | None,
    underlying_exit_px: float | None,
    entry_bid: float | None,
    entry_ask: float | None,
    exit_bid: float | None,
    exit_ask: float | None,
    peak_contract_mid: float | None,
    trough_contract_mid: float | None,
    peak_unrealized_pnl: float | None,
    trough_unrealized_pnl: float | None,
    entry_delta: float | None,
    exit_delta: float | None,
    entry_iv: float | None,
    exit_iv: float | None,
    cfg: AppConfig,
) -> None:
    entry_mid = _mid_from_bid_ask(entry_bid, entry_ask)
    exit_mid = _mid_from_bid_ask(exit_bid, exit_ask)
    entry_dt = _parse_ts(entry_ts, cfg.timezone)
    exit_dt = _parse_ts(exit_ts, cfg.timezone)
    midpoint_dt = entry_dt + (exit_dt - entry_dt) / 2
    late_dt = entry_dt + ((exit_dt - entry_dt) * 2 / 3)
    marks = [
        {
            "run_id": run_id,
            "position_id": position_id,
            "ts": entry_dt.isoformat(),
            "quote_ts": entry_dt.isoformat(),
            "underlying_price": underlying_entry_px,
            "bid": entry_bid,
            "ask": entry_ask,
            "mid": entry_mid,
            "spread_pct": None if entry_mid in (None, 0) else ((float(entry_ask) - float(entry_bid)) / float(entry_mid)),
            "delta": entry_delta,
            "iv": entry_iv,
            "theta": None,
            "vega": None,
            "unrealized_pnl": 0.0,
            "unrealized_return_pct": 0.0,
            "data_json": _json_dumps({"phase": "entry"}),
        }
    ]
    if peak_contract_mid is not None:
        marks.append(
            {
                "run_id": run_id,
                "position_id": position_id,
                "ts": midpoint_dt.isoformat(),
                "quote_ts": midpoint_dt.isoformat(),
                "underlying_price": None,
                "bid": None,
                "ask": None,
                "mid": peak_contract_mid,
                "spread_pct": None,
                "delta": exit_delta,
                "iv": exit_iv,
                "theta": None,
                "vega": None,
                "unrealized_pnl": peak_unrealized_pnl,
                "unrealized_return_pct": None,
                "data_json": _json_dumps({"phase": "peak"}),
            }
        )
    if trough_contract_mid is not None:
        marks.append(
            {
                "run_id": run_id,
                "position_id": position_id,
                "ts": late_dt.isoformat(),
                "quote_ts": late_dt.isoformat(),
                "underlying_price": None,
                "bid": None,
                "ask": None,
                "mid": trough_contract_mid,
                "spread_pct": None,
                "delta": exit_delta,
                "iv": exit_iv,
                "theta": None,
                "vega": None,
                "unrealized_pnl": trough_unrealized_pnl,
                "unrealized_return_pct": None,
                "data_json": _json_dumps({"phase": "trough"}),
            }
        )
    marks.append(
        {
            "run_id": run_id,
            "position_id": position_id,
            "ts": exit_dt.isoformat(),
            "quote_ts": exit_dt.isoformat(),
            "underlying_price": underlying_exit_px,
            "bid": exit_bid,
            "ask": exit_ask,
            "mid": exit_mid,
            "spread_pct": None if exit_mid in (None, 0) else ((float(exit_ask) - float(exit_bid)) / float(exit_mid)),
            "delta": exit_delta,
            "iv": exit_iv,
            "theta": None,
            "vega": None,
            "unrealized_pnl": 0.0,
            "unrealized_return_pct": 0.0,
            "data_json": _json_dumps({"phase": "exit"}),
        }
    )
    for mark in marks:
        _insert(conn, "options_position_marks", mark)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fetch_trade_rows(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT run_id, trade_id, event_id, symbol, option_symbol, direction, variant_id,
               session_date, event_ts, contracts, premium_at_risk, entry_fill, exit_fill,
               net_pnl, gross_pnl, return_pct, exit_reason, holding_minutes,
               entry_delta, exit_delta, entry_iv, exit_iv
        FROM vw_options_trade_research
        WHERE run_id = ?
        ORDER BY event_ts, trade_id
        """,
        (run_id,),
    ).fetchall()
    conn.row_factory = previous_factory
    return [dict(row) for row in rows]


def _aggregate_daily(trades: list[dict[str, Any]], starting_equity: float) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade["session_date"])].append(trade)
    equity = float(starting_equity)
    rows: list[dict[str, Any]] = []
    for session_date in sorted(grouped):
        day_trades = grouped[session_date]
        gross_pnl = sum(float(t["gross_pnl"] or 0.0) for t in day_trades)
        net_pnl = sum(float(t["net_pnl"] or 0.0) for t in day_trades)
        gross_wins = sum(float(t["gross_pnl"] or 0.0) for t in day_trades if float(t["gross_pnl"] or 0.0) > 0)
        gross_losses = sum(float(t["gross_pnl"] or 0.0) for t in day_trades if float(t["gross_pnl"] or 0.0) < 0)
        start_equity = equity
        equity += net_pnl
        rows.append(
            {
                "session_date": session_date,
                "starting_equity": round(start_equity, 2),
                "ending_equity": round(equity, 2),
                "gross_pnl": round(gross_pnl, 2),
                "net_pnl": round(net_pnl, 2),
                "return_pct": round((net_pnl / start_equity) if start_equity else 0.0, 6),
                "trades": len(day_trades),
                "wins": sum(1 for t in day_trades if float(t["net_pnl"] or 0.0) > 0),
                "losses": sum(1 for t in day_trades if float(t["net_pnl"] or 0.0) <= 0),
                "gross_wins": round(gross_wins, 2),
                "gross_losses": round(gross_losses, 2),
                "fees_estimate": round(len(day_trades) * 1.2, 2),
                "slippage_estimate": 0.0,
            }
        )
    return rows


def _aggregate_monthly(daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        grouped[str(row["session_date"])[:7]].append(row)
    out: list[dict[str, Any]] = []
    for month in sorted(grouped):
        rows = grouped[month]
        out.append(
            {
                "month": month,
                "net_pnl": round(sum(float(r["net_pnl"]) for r in rows), 2),
                "return_pct": round(sum(float(r["return_pct"]) for r in rows), 6),
                "trades": sum(int(r["trades"]) for r in rows),
            }
        )
    return out


def _aggregate_simple(trades: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade[key])].append(trade)
    rows: list[dict[str, Any]] = []
    for group_key in sorted(grouped):
        group = grouped[group_key]
        gross_profit = sum(float(t["net_pnl"]) for t in group if float(t["net_pnl"]) > 0)
        gross_loss = -sum(float(t["net_pnl"]) for t in group if float(t["net_pnl"]) < 0)
        pf = None if gross_loss == 0 else gross_profit / gross_loss
        rows.append(
            {
                key: group_key,
                "trades": len(group),
                "wins": sum(1 for t in group if float(t["net_pnl"]) > 0),
                "net_pnl": round(sum(float(t["net_pnl"]) for t in group), 2),
                "avg_return_pct": round(sum(float(t["return_pct"]) for t in group) / len(group), 6),
                "profit_factor": None if pf is None else round(pf, 4),
            }
        )
    return rows

def run_research_replay(
    conn: sqlite3.Connection,
    cfg: AppConfig,
    start: str | None = None,
    end: str | None = None,
    starting_equity: float = 100000.0,
    run_label: str | None = None,
) -> dict[str, Any]:
    started_at = _now_iso(cfg.timezone)
    run_id = _stable_id("run", run_label or "research", start or "ALL_START", end or "ALL_END", started_at)
    output_dir = cfg.resolved_research_output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    _upsert(
        conn,
        "options_strategy_runs",
        "run_id",
        {
            "run_id": run_id,
            "mode": "research_replay",
            "started_at": started_at,
            "completed_at": None,
            "status": "RUNNING",
            "config_json": _json_dumps(
                {
                    "start": start,
                    "end": end,
                    "starting_equity": starting_equity,
                    "run_label": run_label,
                    "config": asdict(cfg.options),
                }
            ),
            "summary_json": None,
        },
    )
    conn.commit()

    signals = _load_research_signals(conn, start=start, end=end)
    current_equity = float(starting_equity)
    accepted = 0
    rejected = 0
    missing_outcomes = 0
    daily_realized_pnl: dict[str, float] = defaultdict(float)
    daily_new_trades: dict[str, int] = defaultdict(int)
    active_positions: list[dict[str, Any]] = []

    def _settle_positions(asof_dt: datetime) -> None:
        nonlocal current_equity, active_positions
        still_open: list[dict[str, Any]] = []
        for pending in active_positions:
            exit_dt = pending["exit_dt"]
            if exit_dt <= asof_dt:
                current_equity += float(pending["net_pnl"])
                exit_session_date = exit_dt.date().isoformat()
                daily_realized_pnl[exit_session_date] += float(pending["net_pnl"])
            else:
                still_open.append(pending)
        active_positions = still_open

    for signal in signals:
        event_dt = _parse_ts(signal.event_ts, cfg.timezone)
        _settle_positions(event_dt)
        session_date = event_dt.date().isoformat()
        open_premium_total = sum(float(position["premium_at_risk"]) for position in active_positions)
        open_symbol_premium = sum(
            float(position["premium_at_risk"]) for position in active_positions if str(position["symbol"]) == str(signal.symbol)
        )
        open_direction_premium = sum(
            float(position["premium_at_risk"]) for position in active_positions if str(position["direction"]) == str(signal.direction)
        )
        chain = _load_chain(conn, signal.event_id or "")
        portfolio_state = PortfolioState(
            cash_available=max(0.0, float(current_equity) - float(open_premium_total)),
            open_premium_total=float(open_premium_total),
            open_symbol_premium=float(open_symbol_premium),
            open_direction_premium=float(open_direction_premium),
            realized_pnl_today=float(daily_realized_pnl[session_date]),
            new_trades_today=int(daily_new_trades[session_date]),
        )
        capture = record_decision_capture(
            conn=conn,
            cfg=cfg,
            signal=signal,
            chain=chain,
            account_equity=current_equity,
            source_provider="research_input_db",
            mode="research_replay",
            run_id=run_id,
            portfolio_state=portfolio_state,
        )
        if not capture["accepted"]:
            rejected += 1
            continue

        accepted += 1
        daily_new_trades[session_date] += 1
        selected_option_symbol = str(capture["selected_contract"])
        outcome = _load_outcome(conn, signal.event_id or "", selected_option_symbol)
        position_id = str(capture["position_id"])
        selection_id = capture["selection_id"]
        if outcome is None:
            missing_outcomes += 1
            conn.execute(
                "UPDATE options_positions SET status = ?, updated_at = ? WHERE position_id = ?",
                ("MISSING_OUTCOME", started_at, position_id),
            )
            _insert(
                conn,
                "options_events",
                {
                    "run_id": run_id,
                    "ts": started_at,
                    "event_id": signal.event_id,
                    "symbol": signal.symbol,
                    "level": "WARNING",
                    "event_type": "missing_research_outcome",
                    "session_date": signal.event_ts[:10],
                    "stage": "research_replay",
                    "position_id": position_id,
                    "trade_id": None,
                    "broker_order_id": None,
                    "message": f"No research outcome found for {selected_option_symbol}",
                    "data_json": _json_dumps({"run_id": run_id}),
                },
            )
            conn.commit()
            continue

        pos = conn.execute(
            "SELECT contracts, premium_at_risk, entry_ts, option_symbol FROM options_positions WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        if pos is None:
            raise RuntimeError(f"Position not found after capture: {position_id}")
        contracts = int(pos[0])
        premium_at_risk = float(pos[1])
        entry_ts = str(pos[2])
        option_symbol = str(pos[3])

        entry_fill = float(outcome["entry_fill"])
        exit_fill = float(outcome["exit_fill"])
        gross_pnl = (exit_fill - entry_fill) * contracts * 100.0
        fees_estimate = float(outcome["fees_estimate"] or 0.0)
        net_pnl = gross_pnl - fees_estimate
        return_pct = 0.0 if premium_at_risk <= 0 else net_pnl / premium_at_risk
        trade_id = _stable_id("trade", run_id, position_id, option_symbol)
        entry_order_id = _stable_id("ord", run_id, position_id, "entry")
        exit_order_id = _stable_id("ord", run_id, position_id, "exit")

        _insert(
            conn,
            "options_orders",
            {
                "run_id": run_id,
                "order_id": entry_order_id,
                "position_id": position_id,
                "event_id": signal.event_id,
                "symbol": signal.symbol,
                "option_symbol": option_symbol,
                "broker_order_id": entry_order_id,
                "client_order_id": entry_order_id,
                "side": "buy",
                "order_type": "limit",
                "tif": "day",
                "limit_price": entry_fill,
                "stop_price": None,
                "qty": contracts,
                "parent_order_id": None,
                "purpose": "entry",
                "status": "filled",
                "filled_qty": contracts,
                "filled_avg_price": entry_fill,
                "submitted_at": entry_ts,
                "updated_at": entry_ts,
                "transition_json": _json_dumps({"status": "filled"}),
                "raw_json": _json_dumps({"mode": "research_replay"}),
            },
        )
        _insert(
            conn,
            "options_orders",
            {
                "run_id": run_id,
                "order_id": exit_order_id,
                "position_id": position_id,
                "event_id": signal.event_id,
                "symbol": signal.symbol,
                "option_symbol": option_symbol,
                "broker_order_id": exit_order_id,
                "client_order_id": exit_order_id,
                "side": "sell",
                "order_type": "limit",
                "tif": "day",
                "limit_price": exit_fill,
                "stop_price": None,
                "qty": contracts,
                "parent_order_id": entry_order_id,
                "purpose": "exit",
                "status": "filled",
                "filled_qty": contracts,
                "filled_avg_price": exit_fill,
                "submitted_at": str(outcome["exit_ts"]),
                "updated_at": str(outcome["exit_ts"]),
                "transition_json": _json_dumps({"status": "filled", "reason": outcome["exit_reason"]}),
                "raw_json": _json_dumps({"mode": "research_replay"}),
            },
        )

        peak_unrealized_pnl = None if outcome["peak_contract_mid"] is None else (float(outcome["peak_contract_mid"]) - entry_fill) * contracts * 100.0
        trough_unrealized_pnl = None if outcome["trough_contract_mid"] is None else (float(outcome["trough_contract_mid"]) - entry_fill) * contracts * 100.0
        _insert_position_marks(
            conn=conn,
            run_id=run_id,
            position_id=position_id,
            entry_ts=entry_ts,
            exit_ts=str(outcome["exit_ts"]),
            underlying_entry_px=signal.underlying_price,
            underlying_exit_px=(None if outcome["underlying_exit_px"] is None else float(outcome["underlying_exit_px"])),
            entry_bid=(None if outcome["entry_bid"] is None else float(outcome["entry_bid"])),
            entry_ask=(None if outcome["entry_ask"] is None else float(outcome["entry_ask"])),
            exit_bid=(None if outcome["exit_bid"] is None else float(outcome["exit_bid"])),
            exit_ask=(None if outcome["exit_ask"] is None else float(outcome["exit_ask"])),
            peak_contract_mid=(None if outcome["peak_contract_mid"] is None else float(outcome["peak_contract_mid"])),
            trough_contract_mid=(None if outcome["trough_contract_mid"] is None else float(outcome["trough_contract_mid"])),
            peak_unrealized_pnl=peak_unrealized_pnl,
            trough_unrealized_pnl=trough_unrealized_pnl,
            entry_delta=(None if outcome["entry_delta"] is None else float(outcome["entry_delta"])),
            exit_delta=(None if outcome["exit_delta"] is None else float(outcome["exit_delta"])),
            entry_iv=(None if outcome["entry_iv"] is None else float(outcome["entry_iv"])),
            exit_iv=(None if outcome["exit_iv"] is None else float(outcome["exit_iv"])),
            cfg=cfg,
        )

        conn.execute(
            """
            UPDATE options_positions
            SET entry_fill = ?, status = ?, broker_order_id = ?, updated_at = ?, max_contract_mid = ?, min_contract_mid = ?,
                max_underlying_price = ?, min_underlying_price = ?, last_mark_ts = ?
            WHERE position_id = ?
            """,
            (
                entry_fill,
                "CLOSED",
                entry_order_id,
                str(outcome["exit_ts"]),
                outcome["peak_contract_mid"],
                outcome["trough_contract_mid"],
                outcome["underlying_peak_px"],
                outcome["underlying_trough_px"],
                str(outcome["exit_ts"]),
                position_id,
            ),
        )

        _insert(
            conn,
            "options_trades",
            {
                "trade_id": trade_id,
                "run_id": run_id,
                "position_id": position_id,
                "symbol": signal.symbol,
                "option_symbol": option_symbol,
                "entry_ts": entry_ts,
                "exit_ts": str(outcome["exit_ts"]),
                "contracts": contracts,
                "entry_fill": entry_fill,
                "exit_fill": exit_fill,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "return_pct": return_pct,
                "exit_reason": str(outcome["exit_reason"]),
                "underlying_exit_px": outcome["underlying_exit_px"],
                "slippage_estimate": float(outcome["entry_slippage_estimate"] or 0.0) + float(outcome["exit_slippage_estimate"] or 0.0),
                "fees_estimate": fees_estimate,
                "created_at": started_at,
                "event_id": signal.event_id,
                "selection_id": selection_id,
                "entry_snapshot_id": None,
                "exit_snapshot_id": None,
                "entry_bid": outcome["entry_bid"],
                "entry_ask": outcome["entry_ask"],
                "entry_mid": _mid_from_bid_ask(outcome["entry_bid"], outcome["entry_ask"], entry_fill),
                "exit_bid": outcome["exit_bid"],
                "exit_ask": outcome["exit_ask"],
                "exit_mid": _mid_from_bid_ask(outcome["exit_bid"], outcome["exit_ask"], exit_fill),
                "entry_delta": outcome["entry_delta"],
                "exit_delta": outcome["exit_delta"],
                "entry_iv": outcome["entry_iv"],
                "exit_iv": outcome["exit_iv"],
                "entry_spread_pct": None,
                "exit_spread_pct": None,
                "underlying_entry_px": outcome["underlying_entry_px"],
                "underlying_stop_px": signal.underlying_stop_price,
                "underlying_peak_px": outcome["underlying_peak_px"],
                "underlying_trough_px": outcome["underlying_trough_px"],
                "max_favorable_excursion": outcome["max_favorable_excursion"],
                "max_adverse_excursion": outcome["max_adverse_excursion"],
                "holding_minutes": outcome["holding_minutes"],
                "entry_slippage_estimate": outcome["entry_slippage_estimate"],
                "exit_slippage_estimate": outcome["exit_slippage_estimate"],
                "contract_multiplier": 100,
                "trade_diagnostics_json": outcome["outcome_json"],
                "peak_contract_mid": outcome["peak_contract_mid"],
                "trough_contract_mid": outcome["trough_contract_mid"],
                "peak_unrealized_pnl": peak_unrealized_pnl,
                "trough_unrealized_pnl": trough_unrealized_pnl,
                "profit_lock_triggered": outcome["profit_lock_triggered"],
                "exit_signal_json": _json_dumps({"split_bucket": outcome["split_bucket"]}),
            },
        )

        _insert(
            conn,
            "options_decision_steps",
            {
                "run_id": run_id,
                "ts": str(outcome["exit_ts"]),
                "event_id": signal.event_id,
                "position_id": position_id,
                "trade_id": trade_id,
                "symbol": signal.symbol,
                "option_symbol": option_symbol,
                "stage": "exit",
                "decision": "closed",
                "reason_code": str(outcome["exit_reason"]),
                "score": net_pnl,
                "data_json": _json_dumps({"return_pct": return_pct}),
            },
        )
        _insert(
            conn,
            "options_events",
            {
                "run_id": run_id,
                "ts": str(outcome["exit_ts"]),
                "event_id": signal.event_id,
                "symbol": signal.symbol,
                "level": "INFO",
                "event_type": "trade_closed",
                "session_date": str(signal.event_ts)[:10],
                "stage": "exit",
                "position_id": position_id,
                "trade_id": trade_id,
                "broker_order_id": exit_order_id,
                "message": f"Closed research trade for {signal.symbol} on {option_symbol}",
                "data_json": _json_dumps({"net_pnl": net_pnl, "exit_reason": outcome["exit_reason"]}),
            },
        )

        exit_dt = _parse_ts(str(outcome["exit_ts"]), cfg.timezone)
        active_positions.append(
            {
                "position_id": position_id,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "premium_at_risk": premium_at_risk,
                "net_pnl": net_pnl,
                "exit_dt": exit_dt,
            }
        )
        snapshot_equity = float(current_equity) + float(net_pnl)
        _insert(
            conn,
            "options_portfolio_snapshots",
            {
                "run_id": run_id,
                "ts": str(outcome["exit_ts"]),
                "session_date": str(signal.event_ts)[:10],
                "mode": "research_replay",
                "cash": snapshot_equity,
                "buying_power": snapshot_equity,
                "portfolio_value": snapshot_equity,
                "options_market_value": 0.0,
                "realized_pnl": net_pnl,
                "unrealized_pnl": 0.0,
                "open_positions": 0,
                "open_orders": 0,
                "exposure_json": _json_dumps({"symbol": signal.symbol, "direction": signal.direction}),
                "broker_account_json": _json_dumps({"split_bucket": outcome["split_bucket"]}),
            },
        )
        conn.commit()

    if active_positions:
        max_dt = max(position["exit_dt"] for position in active_positions)
        _settle_positions(max_dt)

    trades = _fetch_trade_rows(conn, run_id)
    daily_rows = _aggregate_daily(trades, starting_equity)
    monthly_rows = _aggregate_monthly(daily_rows)
    by_symbol_rows = _aggregate_simple(trades, "symbol")
    by_direction_rows = _aggregate_simple(trades, "direction")

    for row in daily_rows:
        metric_id = _stable_id("day", run_id, row["session_date"])
        _upsert(
            conn,
            "options_daily_metrics_runs",
            "metric_id",
            {
                "metric_id": metric_id,
                "session_date": row["session_date"],
                "run_id": run_id,
                "gross_pnl": row["gross_pnl"],
                "net_pnl": row["net_pnl"],
                "return_pct": row["return_pct"],
                "trades": row["trades"],
                "wins": row["wins"],
                "losses": row["losses"],
                "max_drawdown": None,
                "notes_json": _json_dumps({"starting_equity": row["starting_equity"], "ending_equity": row["ending_equity"]}),
                "gross_wins": row["gross_wins"],
                "gross_losses": row["gross_losses"],
                "fees_estimate": row["fees_estimate"],
                "slippage_estimate": row["slippage_estimate"],
                "exposure_json": _json_dumps({"run_id": run_id}),
            },
        )

    gross_profit = sum(float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) > 0)
    gross_loss = -sum(float(t["net_pnl"]) for t in trades if float(t["net_pnl"]) < 0)
    summary = {
        "run_id": run_id,
        "mode": "research_replay",
        "signals_seen": len(signals),
        "trades_closed": len(trades),
        "accepted_signals": accepted,
        "rejected_signals": rejected,
        "missing_outcomes": missing_outcomes,
        "starting_equity": round(float(starting_equity), 2),
        "ending_equity": round(float(starting_equity) + sum(float(t["net_pnl"]) for t in trades), 2),
        "net_pnl": round(sum(float(t["net_pnl"]) for t in trades), 2),
        "win_rate": 0.0 if not trades else round(sum(1 for t in trades if float(t["net_pnl"]) > 0) / len(trades), 4),
        "profit_factor": None if gross_loss == 0 else round(gross_profit / gross_loss, 4),
        "avg_trade_return_pct": 0.0 if not trades else round(sum(float(t["return_pct"]) for t in trades) / len(trades), 6),
        "output_dir": str(output_dir),
    }

    _write_csv(output_dir / "trades.csv", trades)
    _write_csv(output_dir / "daily_metrics.csv", daily_rows)
    _write_csv(output_dir / "monthly_returns.csv", monthly_rows)
    _write_csv(output_dir / "by_symbol.csv", by_symbol_rows)
    _write_csv(output_dir / "by_direction.csv", by_direction_rows)
    (output_dir / "summary.json").write_text(_json_dumps(summary), encoding="utf-8")

    _upsert(
        conn,
        "options_strategy_runs",
        "run_id",
        {
            "run_id": run_id,
            "mode": "research_replay",
            "started_at": started_at,
            "completed_at": _now_iso(cfg.timezone),
            "status": "COMPLETED",
            "config_json": _json_dumps(
                {
                    "start": start,
                    "end": end,
                    "starting_equity": starting_equity,
                    "run_label": run_label,
                    "config": asdict(cfg.options),
                }
            ),
            "summary_json": _json_dumps(summary),
        },
    )
    conn.commit()
    return summary
