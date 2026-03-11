from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

from agent.broker import AlpacaTradingClient

from .breakout_engine import evaluate_bar_close_only
from .breakouts import HorizonState, ensure_breakout_tables, load_broken_horizons, load_rr_rows
from .config_loader import load_config
from .db import connect, init_db
from .ingest_yf import run_ingest
from .notifier import load_templates, send_discord, send_discord_file
from .prepare_asof import ensure_asof_ready
from .symbols import load_symbols
from .time_utils import combine_cst_date_time

CST = ZoneInfo("America/Chicago")


def _signed_qty_from_position(pos: dict | None) -> int:
    if not pos:
        return 0
    try:
        qty = int(abs(float(pos.get("qty", 0.0))))
    except Exception:
        return 0
    side = str(pos.get("side", "")).lower()
    return qty if side == "long" else (-qty if side == "short" else 0)


def _is_session_day(calendar_name: str, cst_date: str) -> bool:
    cal = mcal.get_calendar(calendar_name)
    sched = cal.schedule(start_date=cst_date, end_date=cst_date)
    return not sched.empty


def _interval_minutes(interval: str) -> int:
    s = interval.strip().lower()
    if s.endswith("m"):
        return int(s[:-1])
    raise ValueError(f"Unsupported interval: {interval} (expected like '15m')")


def _last_complete_close_ts(now_cst: datetime, interval: str, session_start_dt: datetime) -> Optional[pd.Timestamp]:
    mins = _interval_minutes(interval)
    safe_now = pd.Timestamp(now_cst) - pd.Timedelta(minutes=1)
    anchor = pd.Timestamp(session_start_dt)
    first_close = anchor + pd.Timedelta(minutes=mins)
    if safe_now < first_close:
        return None
    n = int(((safe_now - anchor).total_seconds()) // (mins * 60))
    return anchor + pd.Timedelta(minutes=n * mins)


def _load_day_candles_for_symbol(conn: sqlite3.Connection, symbol: str, interval: str, cst_date: str) -> pd.DataFrame:
    q = """
    SELECT open_ts_cst, close_ts_cst, open, high, low, close
    FROM candles
    WHERE symbol=? AND interval=? AND cst_date=?
    ORDER BY open_ts_cst
    """
    df = pd.read_sql_query(q, conn, params=[symbol, interval, cst_date])
    if df.empty:
        return df
    df["open_ts"] = pd.to_datetime(df["open_ts_cst"], utc=True)
    df["close_ts"] = pd.to_datetime(df["close_ts_cst"], utc=True)
    df["ema20"] = df["close"].astype(float).ewm(span=20, adjust=False).mean()
    df["ema20_prev"] = df["ema20"].shift(1).fillna(df["ema20"])
    return df


def _ensure_paper_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_paper_positions (
            position_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            session_date TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_ts TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            trail_price REAL NOT NULL,
            risk REAL NOT NULL,
            confidence REAL,
            primary_horizon INTEGER,
            include_today_or INTEGER,
            data_provider TEXT NOT NULL,
            broker_order_id TEXT,
            stop_order_id TEXT,
            status TEXT NOT NULL,
            last_bar_ts TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_paper_trades (
            trade_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            session_date TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_ts TEXT NOT NULL,
            exit_ts TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            risk REAL NOT NULL,
            pnl REAL NOT NULL,
            pnl_pct REAL NOT NULL,
            r_mult REAL NOT NULL,
            exit_reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_paper_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            symbol TEXT,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            data_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS r6_paper_missed_trades (
            miss_id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            session_date TEXT,
            strategy_id TEXT,
            side TEXT,
            signal_ts TEXT,
            entry_price REAL,
            stop_price REAL,
            risk REAL,
            planned_qty INTEGER,
            reason TEXT NOT NULL,
            data_json TEXT
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info('r6_paper_positions')").fetchall()}
    if "stop_order_id" not in cols:
        conn.execute("ALTER TABLE r6_paper_positions ADD COLUMN stop_order_id TEXT")
    conn.commit()


def _log_event(
    conn: sqlite3.Connection,
    level: str,
    event_type: str,
    message: str,
    symbol: str | None = None,
    data: dict | None = None,
    discord_webhook: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO r6_paper_events (ts, symbol, level, event_type, message, data_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(CST).isoformat(),
            symbol,
            level,
            event_type,
            message,
            (pd.Series(data or {}).to_json() if data else None),
        ),
    )
    conn.commit()
    if discord_webhook and level in {"WARN", "ERROR"}:
        level_emoji = {"WARN": "⚠️", "ERROR": "🚨"}.get(level, "ℹ️")
        lines = [
            f"{level_emoji} **[R6] {event_type}**",
            f"🕒 **Time:** {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"📌 **Symbol:** {symbol or '-'} | **Level:** {level}",
            f"💬 **Message:** {message}",
        ]
        if data:
            bits: list[str] = []
            for k in ("strategy_id", "side", "qty", "entry_price", "stop_price", "attempt", "max_attempts", "reason", "error"):
                if k in data:
                    bits.append(f"{k}={data[k]}")
            if bits:
                lines.append(f"🧾 **Details:** {' | '.join(bits)}")
        txt = "\n".join(lines)
        try:
            send_discord(discord_webhook, txt[:1800])
        except Exception:
            pass


def _notify_trade(webhook: str | None, title: str, symbol: str, details: dict) -> None:
    if not webhook:
        return
    title_norm = str(title).lower()
    title_emoji = "✅" if "entry" in title_norm else ("🏁" if "closed" in title_norm else "📣")
    side = str(details.get("side") or "").upper()
    side_emoji = "🟢" if side == "LONG" else ("🔴" if side == "SHORT" else "⚪")
    lines = [
        f"{title_emoji} **[R6] {title}**",
        f"🕒 **Time:** {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"📌 **Symbol:** {symbol}",
    ]
    if side:
        lines.append(f"🧭 **Side:** {side_emoji} {side}")
    if "strategy_id" in details:
        lines.append(f"🧠 **Strategy:** `{details['strategy_id']}`")
    if "exit_strategy" in details:
        lines.append(f"⚙️ **Exit Plan:** {details['exit_strategy']}")
    if "time_exit_ct" in details:
        lines.append(f"🕘 **Time Exit:** {details['time_exit_ct']} CT")

    trade_bits: list[str] = []
    for k in ("qty", "entry_ts", "entry_price", "stop_price", "risk"):
        if k in details:
            trade_bits.append(f"{k}={details[k]}")
    if trade_bits:
        lines.append(f"💼 **Trade:** {' | '.join(trade_bits)}")

    ref_bits: list[str] = []
    for k in ("primary_horizon", "include_today_or", "ref_high", "ref_low", "ref_width", "confidence"):
        if k in details:
            ref_bits.append(f"{k}={details[k]}")
    if ref_bits:
        lines.append(f"📏 **Reference:** {' | '.join(ref_bits)}")

    protect_bits: list[str] = []
    for k in ("stop_order_attached", "broker_order_id", "stop_order_id", "position_id", "event_id"):
        if k in details:
            protect_bits.append(f"{k}={details[k]}")
    if protect_bits:
        lines.append(f"🛡️ **Execution:** {' | '.join(protect_bits)}")

    exit_bits: list[str] = []
    for k in ("exit_price", "reason", "pnl", "r_mult"):
        if k in details:
            exit_bits.append(f"{k}={details[k]}")
    if exit_bits:
        lines.append(f"🏁 **Exit:** {' | '.join(exit_bits)}")

    if "engine_version" in details:
        lines.append(f"🧪 **Engine:** {details['engine_version']}")
    try:
        send_discord(webhook, "\n".join(lines)[:1800])
    except Exception:
        pass


def _exit_strategy_text(strategy_id: str, time_exit_hhmm: str | None) -> str:
    sid = str(strategy_id or "")
    core = sid.split("__", 1)[1] if "__" in sid else sid
    core = core or "EMA20_TRAIL_ONLY"
    if "EMA20_TRAIL" in core:
        trail = "Trail under 15m EMA20"
    elif "2R" in core:
        trail = "Fixed 2R + stop"
    else:
        trail = core.replace("_", " ")
    if time_exit_hhmm:
        return f"{trail} + time exit {time_exit_hhmm} CT"
    return trail


def _render_dashboard(
    *,
    enabled: bool,
    cfg_path: str,
    strategy_id: str,
    session_date: str,
    dry_run: bool,
    provider: str,
    symbols: list[str],
    cycle_count: int,
    bars_processed: int,
    entries_opened: int,
    positions_closed: int,
    open_positions_local: int,
    open_positions_broker: int,
    last_bar_close: str | None,
    last_action: str,
) -> None:
    if not enabled:
        return
    now_ct = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %Z")
    print("\033[2J\033[H", end="")
    print("R6 PAPER LIVE DASHBOARD")
    print("=" * 72)
    print(f"Now: {now_ct} | Session Date: {session_date}")
    print(f"Config: {cfg_path} | DryRun: {dry_run} | Provider: {provider}")
    print(f"Variant: {strategy_id}")
    print(f"Cycles: {cycle_count} | Bars Processed: {bars_processed}")
    print(f"Entries Opened: {entries_opened} | Positions Closed: {positions_closed}")
    print(f"Open Positions (DB/Broker): {open_positions_local}/{open_positions_broker}")
    print(f"Last Bar Close: {last_bar_close or '-'}")
    print(f"Last Action: {last_action}")
    print(f"Symbols: {','.join(symbols)}")
    print("=" * 72)


def _fetch_open_position(conn: sqlite3.Connection, symbol: str) -> dict | None:
    row = conn.execute(
        """
        SELECT *
        FROM r6_paper_positions
        WHERE symbol=? AND status='OPEN'
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute("PRAGMA table_info('r6_paper_positions')").fetchall()]
    # PRAGMA returns cid,name,type,...; index 1 has name
    names = [d[1] for d in conn.execute("PRAGMA table_info('r6_paper_positions')").fetchall()]
    return dict(zip(names, row))


def _fetch_open_positions(conn: sqlite3.Connection, session_date: str | None = None) -> list[dict]:
    if session_date:
        rows = conn.execute(
            """
            SELECT *
            FROM r6_paper_positions
            WHERE status='OPEN' AND session_date=?
            ORDER BY symbol, entry_ts
            """,
            (session_date,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM r6_paper_positions
            WHERE status='OPEN'
            ORDER BY symbol, entry_ts
            """
        ).fetchall()
    if not rows:
        return []
    names = [d[1] for d in conn.execute("PRAGMA table_info('r6_paper_positions')").fetchall()]
    return [dict(zip(names, row)) for row in rows]


def _count_entries_today(conn: sqlite3.Connection, symbol: str, session_date: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM r6_paper_positions
        WHERE symbol=? AND session_date=?
        """,
        (symbol, session_date),
    ).fetchone()
    return int(row[0]) if row else 0


def _count_open_positions(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM r6_paper_positions
        WHERE status='OPEN'
        """
    ).fetchone()
    return int(row[0]) if row else 0


def _estimate_symbol_exit_price(
    conn: sqlite3.Connection,
    broker: AlpacaTradingClient | None,
    symbol: str,
    interval: str,
    session_date: str,
    fallback_price: float,
) -> float:
    if broker is not None:
        try:
            pos = broker.get_open_position(symbol)
            if pos:
                for k in ("current_price", "avg_entry_price", "lastday_price"):
                    v = pos.get(k)
                    if v is None:
                        continue
                    try:
                        px = float(v)
                        if px > 0:
                            return px
                    except Exception:
                        continue
        except Exception:
            pass
    row = conn.execute(
        """
        SELECT close
        FROM candles
        WHERE symbol=? AND interval=? AND cst_date=?
        ORDER BY close_ts_cst DESC
        LIMIT 1
        """,
        (symbol, interval, session_date),
    ).fetchone()
    if row is not None:
        try:
            px = float(row[0])
            if px > 0:
                return px
        except Exception:
            pass
    return float(fallback_price)


def _account_equity(broker: AlpacaTradingClient | None, dry_run: bool, default_equity: float) -> float:
    if dry_run or broker is None:
        return float(default_equity)
    acc = broker.get_account()
    return float(acc.get("equity") or default_equity)


def _confidence_scale(confidence: float | None, floor: float, full: float, min_mult: float) -> float:
    if confidence is None:
        return max(0.0, min(1.0, min_mult))
    lo = float(floor)
    hi = max(float(full), lo + 1e-6)
    base = max(0.0, min(1.0, float(min_mult)))
    x = (float(confidence) - lo) / (hi - lo)
    x = max(0.0, min(1.0, x))
    return base + (1.0 - base) * x


def _compute_qty(
    equity: float,
    risk_pct: float,
    max_notional_pct: float,
    max_notional_dollars: float,
    entry_price: float,
    stop_price: float,
    *,
    confidence: float | None = None,
    confidence_sizing_enabled: bool = False,
    confidence_floor: float = 0.62,
    confidence_full: float = 0.85,
    confidence_min_multiplier: float = 0.50,
) -> int:
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return 0
    scale = (
        _confidence_scale(confidence, confidence_floor, confidence_full, confidence_min_multiplier)
        if confidence_sizing_enabled
        else 1.0
    )
    risk_budget = equity * risk_pct * scale
    qty_by_risk = int(risk_budget // risk_per_share)
    qty_by_notional_pct = int((equity * max_notional_pct * scale) // entry_price) if entry_price > 0 else 0
    qty_by_notional_abs = int(max(0.0, max_notional_dollars * scale) // entry_price) if entry_price > 0 else 0
    return max(0, min(qty_by_risk, qty_by_notional_pct, qty_by_notional_abs))


def _r_at_price(side: str, entry_price: float, exit_price: float, risk: float) -> float:
    if side == "LONG":
        return (exit_price - entry_price) / risk
    return (entry_price - exit_price) / risk


def _stop_fill_price(side: str, stop_price: float, bar_open: float) -> float:
    if side == "LONG":
        return min(stop_price, bar_open)
    return max(stop_price, bar_open)


def _is_short_unavailable_error(exc: Exception) -> bool:
    txt = str(exc).lower()
    needles = (
        "not shortable",
        "insufficient qty available for short sale",
        "cannot be sold short",
        "short sale",
    )
    return any(n in txt for n in needles)


def _available_long_qty(broker: AlpacaTradingClient | None, symbol: str) -> int:
    if broker is None:
        return 0
    pos = broker.get_open_position(symbol)
    if not pos:
        return 0
    if str(pos.get("side", "")).lower() != "long":
        return 0
    try:
        qty = int(abs(float(pos.get("qty", 0.0))))
    except Exception:
        qty = 0
    return max(0, qty)


def _broker_position_for_symbol(broker: AlpacaTradingClient | None, symbol: str) -> dict | None:
    if broker is None:
        return None
    try:
        return broker.get_open_position(symbol)
    except Exception:
        return None


def _count_broker_open_positions(broker: AlpacaTradingClient | None, dry_run: bool) -> int:
    if dry_run or broker is None:
        return 0
    try:
        return int(len(broker.list_positions() or []))
    except Exception:
        return 0


def _record_missed_trade(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    session_date: str,
    strategy_id: str,
    side: str,
    signal_ts: str,
    entry_price: float,
    stop_price: float,
    risk: float,
    planned_qty: int,
    reason: str,
    data: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO r6_paper_missed_trades
        (miss_id, ts, symbol, session_date, strategy_id, side, signal_ts, entry_price, stop_price, risk, planned_qty, reason, data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            datetime.now(CST).isoformat(),
            symbol,
            session_date,
            strategy_id,
            side,
            signal_ts,
            float(entry_price),
            float(stop_price),
            float(risk),
            int(planned_qty),
            reason,
            (pd.Series(data or {}).to_json() if data else None),
        ),
    )
    conn.commit()


def _cancel_open_orders_for_symbol(broker: AlpacaTradingClient | None, symbol: str) -> int:
    if broker is None:
        return 0
    canceled = 0
    try:
        orders = broker.list_open_orders(symbol=symbol) or []
    except Exception:
        return 0
    for o in orders:
        oid = str(o.get("id") or "").strip()
        if not oid:
            continue
        try:
            broker.cancel_order(oid)
            canceled += 1
        except Exception:
            continue
    return canceled


def _wait_for_order_to_leave_open_book(
    broker: AlpacaTradingClient | None,
    *,
    symbol: str,
    order_id: str | None,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.5,
) -> bool:
    if broker is None:
        return True
    oid = str(order_id or "").strip()
    if not oid:
        return True
    end = time.time() + max(0.1, float(timeout_seconds))
    while time.time() < end:
        try:
            open_orders = broker.list_open_orders(symbol=symbol) or []
            is_open = any(str(o.get("id") or "").strip() == oid for o in open_orders)
            if not is_open:
                return True
        except Exception:
            pass
        time.sleep(max(0.1, float(poll_seconds)))
    return False


def _wait_for_symbol_flat(
    broker: AlpacaTradingClient | None,
    *,
    symbol: str,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.5,
) -> bool:
    if broker is None:
        return True
    end = time.time() + max(0.1, float(timeout_seconds))
    while time.time() < end:
        try:
            pos = _broker_position_for_symbol(broker, symbol)
            if pos is None or _signed_qty_from_position(pos) == 0:
                return True
        except Exception:
            pass
        time.sleep(max(0.1, float(poll_seconds)))
    return False


def _attempt_force_flatten_symbol(
    broker: AlpacaTradingClient | None,
    *,
    symbol: str,
    attempts: int = 3,
    sleep_seconds: float = 0.75,
) -> bool:
    if broker is None:
        return True
    if _wait_for_symbol_flat(broker, symbol=symbol, timeout_seconds=0.3, poll_seconds=0.1):
        return True
    for _ in range(max(1, int(attempts))):
        try:
            broker.close_position(symbol)
        except Exception:
            pass
        time.sleep(max(0.1, float(sleep_seconds)))
        if _wait_for_symbol_flat(broker, symbol=symbol, timeout_seconds=1.0, poll_seconds=0.2):
            return True
    return False


def _restore_symbol_exposure(
    broker: AlpacaTradingClient | None,
    *,
    symbol: str,
    target_signed_qty: int,
) -> dict:
    if broker is None:
        return {"restored": False, "reason": "no_broker"}
    current_pos = _broker_position_for_symbol(broker, symbol)
    current_signed = _signed_qty_from_position(current_pos)
    delta = int(target_signed_qty) - int(current_signed)
    if delta == 0:
        return {"restored": True, "target_signed_qty": int(target_signed_qty), "current_signed_qty": int(current_signed)}
    side = "buy" if delta > 0 else "sell"
    qty = abs(int(delta))
    oid = f"r6-restore-{symbol}-{uuid.uuid4().hex[:16]}"
    try:
        broker.submit_market_order(symbol=symbol, side=side, qty=qty, client_order_id=oid)
        time.sleep(0.5)
        after = _broker_position_for_symbol(broker, symbol)
        after_signed = _signed_qty_from_position(after)
        return {
            "restored": after_signed == int(target_signed_qty),
            "target_signed_qty": int(target_signed_qty),
            "before_signed_qty": int(current_signed),
            "after_signed_qty": int(after_signed),
            "order_side": side,
            "order_qty": int(qty),
        }
    except Exception as exc:
        return {
            "restored": False,
            "target_signed_qty": int(target_signed_qty),
            "before_signed_qty": int(current_signed),
            "error": str(exc),
        }


def _try_enter_from_event(
    conn: sqlite3.Connection,
    broker: AlpacaTradingClient | None,
    *,
    event_id: str,
    symbol: str,
    session_date: str,
    strategy_id: str,
    dry_run: bool,
    risk_pct: float,
    max_notional_pct: float,
    max_notional_dollars: float,
    default_equity: float,
    stop_buffer: float,
    short_requires_inventory: bool,
    confidence_sizing_enabled: bool,
    confidence_floor: float,
    confidence_full: float,
    confidence_min_multiplier: float,
    provider: str,
    discord_webhook: str | None,
    time_exit_hhmm: str,
    latest_complete_utc: pd.Timestamp | None,
    interval_minutes: int,
    entry_max_age_bars: int,
) -> bool:
    ev = conn.execute(
        """
        SELECT
            decision, confidence, include_today_or, primary_horizon,
            candle_open, candle_high, candle_low, candle_close, bar_close_ts_cst,
            ref_high, ref_low, ref_width, reasons_json, engine_version
        FROM breakout_events
        WHERE event_id=?
        """,
        (event_id,),
    ).fetchone()
    if ev is None:
        return False
    (
        decision,
        confidence,
        include_today_or,
        primary_horizon,
        candle_open,
        candle_high,
        candle_low,
        candle_close,
        bar_close_ts,
        ref_high,
        ref_low,
        ref_width,
        reasons_json,
        engine_version,
    ) = ev
    if decision not in ("LONG", "SHORT"):
        return False
    if confidence is None or float(confidence) < 0.62:
        return False
    try:
        sig_ts_utc = pd.to_datetime(str(bar_close_ts), utc=True)
    except Exception:
        sig_ts_utc = None
    if sig_ts_utc is not None and latest_complete_utc is not None:
        max_age_bars = max(1, int(entry_max_age_bars))
        max_age_min = max(1, int(interval_minutes) * max_age_bars)
        age_min = (pd.Timestamp(latest_complete_utc) - sig_ts_utc).total_seconds() / 60.0
        if age_min > float(max_age_min):
            _record_missed_trade(
                conn,
                symbol=symbol,
                session_date=session_date,
                strategy_id=strategy_id,
                side=str(decision),
                signal_ts=str(bar_close_ts),
                entry_price=float(candle_close),
                stop_price=float(candle_close),
                risk=0.0,
                planned_qty=0,
                reason="STALE_SIGNAL_SKIPPED",
                data={
                    "signal_age_min": round(float(age_min), 3),
                    "max_age_min": int(max_age_min),
                    "latest_complete_utc": str(pd.Timestamp(latest_complete_utc)),
                },
            )
            _log_event(
                conn,
                "WARN",
                "stale_signal_skipped",
                f"Skipped stale signal for {symbol}",
                symbol=symbol,
                data={
                    "signal_ts": str(bar_close_ts),
                    "signal_age_min": round(float(age_min), 3),
                    "max_age_min": int(max_age_min),
                },
                discord_webhook=None,
            )
            return False
    if str(decision) == "LONG" and int(include_today_or) == 0:
        return False
    if _count_entries_today(conn, symbol, session_date) >= 1:
        return False
    if _fetch_open_position(conn, symbol) is not None:
        return False

    entry_price = float(candle_close)
    if decision == "LONG":
        stop_price = min(float(candle_low) - stop_buffer, entry_price - 0.01)
    else:
        stop_price = max(float(candle_high) + stop_buffer, entry_price + 0.01)
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return False

    equity = _account_equity(broker, dry_run, default_equity)
    qty = _compute_qty(
        equity,
        risk_pct,
        max_notional_pct,
        max_notional_dollars,
        entry_price,
        stop_price,
        confidence=(float(confidence) if confidence is not None else None),
        confidence_sizing_enabled=confidence_sizing_enabled,
        confidence_floor=confidence_floor,
        confidence_full=confidence_full,
        confidence_min_multiplier=confidence_min_multiplier,
    )
    if qty <= 0:
        return False

    if decision == "SHORT" and short_requires_inventory and not dry_run:
        avail = _available_long_qty(broker, symbol)
        if avail < int(qty):
            _record_missed_trade(
                conn,
                symbol=symbol,
                session_date=session_date,
                strategy_id=strategy_id,
                side="SHORT",
                signal_ts=str(bar_close_ts),
                entry_price=entry_price,
                stop_price=stop_price,
                risk=risk,
                planned_qty=int(qty),
                reason="SHORT_BLOCKED_NO_INVENTORY",
                data={
                    "available_long_qty": int(avail),
                    "confidence": float(confidence or 0.0),
                    "primary_horizon": int(primary_horizon),
                },
            )
            _log_event(
                conn,
                "WARN",
                "short_blocked_no_inventory",
                f"Blocked SHORT {symbol}: required qty={qty}, available long qty={avail}",
                symbol=symbol,
                data={
                    "strategy_id": strategy_id,
                    "planned_qty": int(qty),
                    "available_long_qty": int(avail),
                },
                discord_webhook=discord_webhook,
            )
            return False

    broker_order_id = None
    stop_order_id = None
    pre_signed_qty = 0
    pre_side = ""
    stop_attach_attempts = 5
    stop_attach_delay_sec = 1.0
    if not dry_run and broker is not None:
        pre_pos = _broker_position_for_symbol(broker, symbol)
        pre_signed_qty = _signed_qty_from_position(pre_pos)
        pre_side = str((pre_pos or {}).get("side") or "")
        try:
            _cancel_open_orders_for_symbol(broker, symbol)
            side = "buy" if decision == "LONG" else "sell"
            oid = f"r6-{symbol}-{uuid.uuid4().hex[:20]}"
            order = broker.submit_market_order(symbol=symbol, side=side, qty=qty, client_order_id=oid)
            broker_order_id = str(order.get("id") or "")
            _wait_for_order_to_leave_open_book(broker, symbol=symbol, order_id=broker_order_id, timeout_seconds=8.0, poll_seconds=0.5)

            stop_side = "sell" if decision == "LONG" else "buy"
            last_stop_error = None
            for attempt in range(1, stop_attach_attempts + 1):
                _cancel_open_orders_for_symbol(broker, symbol)
                stop_oid = f"r6-stop-{symbol}-{uuid.uuid4().hex[:16]}"
                try:
                    stop_order = broker.submit_stop_order(
                        symbol=symbol,
                        side=stop_side,
                        qty=qty,
                        stop_price=stop_price,
                        client_order_id=stop_oid,
                    )
                    stop_order_id = str(stop_order.get("id") or "")
                    if stop_order_id:
                        break
                except Exception as exc:
                    last_stop_error = exc
                    _log_event(
                        conn,
                        "WARN",
                        "stop_attach_retry",
                        f"{symbol} stop attach retry {attempt}/{stop_attach_attempts}: {exc}",
                        symbol=symbol,
                        data={"attempt": int(attempt), "max_attempts": int(stop_attach_attempts)},
                        discord_webhook=None,
                    )
                    time.sleep(stop_attach_delay_sec)

            if not stop_order_id:
                restore = _restore_symbol_exposure(
                    broker,
                    symbol=symbol,
                    target_signed_qty=pre_signed_qty,
                )
                _record_missed_trade(
                    conn,
                    symbol=symbol,
                    session_date=session_date,
                    strategy_id=strategy_id,
                    side=str(decision),
                    signal_ts=str(bar_close_ts),
                    entry_price=entry_price,
                    stop_price=stop_price,
                    risk=risk,
                    planned_qty=int(qty),
                    reason="STOP_ATTACH_FAILED_FLATTENED",
                    data={
                        "error": str(last_stop_error) if last_stop_error is not None else "unknown",
                        "pre_signed_qty": int(pre_signed_qty),
                        "pre_side": pre_side,
                        "restore_result": restore,
                    },
                )
                _log_event(
                    conn,
                    "ERROR",
                    "stop_attach_failed_flattened",
                    f"{symbol} stop attach failed after retries; exposure restoration attempted",
                    symbol=symbol,
                    data={
                        "attempts": int(stop_attach_attempts),
                        "error": str(last_stop_error) if last_stop_error is not None else "unknown",
                        "restore_result": restore,
                    },
                    discord_webhook=discord_webhook,
                )
                return False
        except Exception as exc:
            reason = "SHORT_NOT_AVAILABLE" if (str(decision) == "SHORT" and _is_short_unavailable_error(exc)) else "ENTRY_SUBMIT_FAILED"
            _record_missed_trade(
                conn,
                symbol=symbol,
                session_date=session_date,
                strategy_id=strategy_id,
                side=str(decision),
                signal_ts=str(bar_close_ts),
                entry_price=entry_price,
                stop_price=stop_price,
                risk=risk,
                planned_qty=int(qty),
                reason=reason,
                data={"error": str(exc)[:1000]},
            )
            _log_event(
                conn,
                "WARN" if reason == "SHORT_NOT_AVAILABLE" else "ERROR",
                "short_not_available" if reason == "SHORT_NOT_AVAILABLE" else "entry_submit_failed",
                f"{symbol} entry submit failed: {exc}",
                symbol=symbol,
                data={"side": str(decision), "qty": int(qty), "reason": reason},
                discord_webhook=discord_webhook,
            )
            return False

    ts_now = datetime.now(CST).isoformat()
    position_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO r6_paper_positions (
            position_id, symbol, strategy_id, session_date, side, qty, entry_ts, entry_price,
            stop_price, trail_price, risk, confidence, primary_horizon, include_today_or,
            data_provider, broker_order_id, stop_order_id, status, last_bar_ts, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
        """,
        (
            position_id,
            symbol,
            strategy_id,
            session_date,
            decision,
            int(qty),
            str(bar_close_ts),
            entry_price,
            stop_price,
            stop_price,
            risk,
            float(confidence),
            int(primary_horizon),
            int(include_today_or),
            provider,
            broker_order_id,
            stop_order_id,
            str(bar_close_ts),
            ts_now,
            ts_now,
        ),
    )
    conn.commit()
    _log_event(
        conn,
        "INFO",
        "entry_opened",
        f"Opened {decision} {symbol} qty={qty}",
        symbol=symbol,
        data={
            "position_id": position_id,
            "event_id": event_id,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "risk": risk,
            "entry_ts": str(bar_close_ts),
            "decision": decision,
            "ref_high": float(ref_high),
            "ref_low": float(ref_low),
            "ref_width": float(ref_width),
            "confidence": float(confidence),
            "primary_horizon": int(primary_horizon),
            "include_today_or": int(include_today_or),
            "candle_open": float(candle_open),
            "candle_high": float(candle_high),
            "candle_low": float(candle_low),
            "candle_close": float(candle_close),
            "reasons_json": str(reasons_json or ""),
            "engine_version": str(engine_version or ""),
            "confidence_scale": (
                round(
                    _confidence_scale(
                        float(confidence) if confidence is not None else None,
                        confidence_floor,
                        confidence_full,
                        confidence_min_multiplier,
                    ),
                    4,
                )
                if confidence_sizing_enabled
                else 1.0
            ),
        },
        discord_webhook=None,
    )
    _notify_trade(
        discord_webhook,
        "entry_opened",
        symbol,
        {
            "side": decision,
            "strategy_id": strategy_id,
            "qty": int(qty),
            "entry_ts": str(bar_close_ts),
            "entry_price": float(entry_price),
            "stop_price": float(stop_price),
            "risk": round(float(risk), 4),
            "include_today_or": int(include_today_or),
            "ref_high": round(float(ref_high), 4),
            "ref_low": round(float(ref_low), 4),
            "ref_width": round(float(ref_width), 4),
            "confidence": round(float(confidence), 4),
            "primary_horizon": int(primary_horizon),
            "engine_version": str(engine_version or ""),
            "position_id": position_id,
            "event_id": event_id,
            "broker_order_id": broker_order_id,
            "stop_order_id": stop_order_id,
            "stop_order_attached": bool(stop_order_id),
            "exit_strategy": _exit_strategy_text(strategy_id, time_exit_hhmm),
            "time_exit_ct": time_exit_hhmm,
            "confidence_scale": (
                round(
                    _confidence_scale(
                        float(confidence) if confidence is not None else None,
                        confidence_floor,
                        confidence_full,
                        confidence_min_multiplier,
                    ),
                    4,
                )
                if confidence_sizing_enabled
                else 1.0
            ),
        },
    )
    return True


def _close_position(
    conn: sqlite3.Connection,
    broker: AlpacaTradingClient | None,
    open_pos: dict,
    exit_price: float,
    exit_ts: str,
    exit_reason: str,
    dry_run: bool,
    discord_webhook: str | None,
    send_broker_exit: bool = True,
    time_exit_hhmm: str | None = None,
) -> bool:
    symbol = str(open_pos["symbol"])
    side = str(open_pos["side"])
    qty = int(open_pos["qty"])
    entry_price = float(open_pos["entry_price"])
    risk = float(open_pos["risk"])

    attempted_broker_exit = bool((not dry_run) and broker is not None and send_broker_exit)
    if not dry_run and broker is not None:
        try:
            stop_order_id = str(open_pos.get("stop_order_id") or "")
            if stop_order_id:
                try:
                    broker.cancel_order(stop_order_id)
                except Exception:
                    pass
            if send_broker_exit:
                broker_pos = _broker_position_for_symbol(broker, symbol)
                if broker_pos is not None:
                    broker_signed = _signed_qty_from_position(broker_pos)
                    expected_sign = 1 if side == "LONG" else -1
                    if broker_signed == 0:
                        send_broker_exit = False
                    elif (1 if broker_signed > 0 else -1) != expected_sign:
                        try:
                            broker.close_position(symbol)
                            send_broker_exit = False
                        except Exception:
                            send_broker_exit = False
                    else:
                        qty = min(int(qty), abs(int(broker_signed)))
                        if qty <= 0:
                            send_broker_exit = False
                else:
                    send_broker_exit = False
                if send_broker_exit:
                    _cancel_open_orders_for_symbol(broker, symbol)
                    exit_side = "sell" if side == "LONG" else "buy"
                    exit_oid = f"r6-exit-{symbol}-{uuid.uuid4().hex[:16]}"
                    exit_order = broker.submit_market_order(symbol=symbol, side=exit_side, qty=qty, client_order_id=exit_oid)
                    _wait_for_order_to_leave_open_book(
                        broker,
                        symbol=symbol,
                        order_id=str((exit_order or {}).get("id") or ""),
                        timeout_seconds=6.0,
                        poll_seconds=0.4,
                    )
        except Exception:
            try:
                broker.close_position(symbol)
            except Exception:
                pass

    if attempted_broker_exit:
        flat_ok = _attempt_force_flatten_symbol(broker, symbol=symbol, attempts=3, sleep_seconds=0.75)
        if not flat_ok:
            broker_pos = _broker_position_for_symbol(broker, symbol) if broker is not None else None
            residual_qty = abs(int(_signed_qty_from_position(broker_pos)))
            if residual_qty > 0:
                open_pos["qty"] = int(residual_qty)
                # Re-attach protective stop for remaining broker qty (best effort).
                if (not dry_run) and broker is not None:
                    try:
                        stop_side = "sell" if side == "LONG" else "buy"
                        stop_oid = f"r6-stop-residual-{symbol}-{uuid.uuid4().hex[:16]}"
                        st = broker.submit_stop_order(
                            symbol=symbol,
                            side=stop_side,
                            qty=int(residual_qty),
                            stop_price=float(open_pos["stop_price"]),
                            client_order_id=stop_oid,
                        )
                        open_pos["stop_order_id"] = str(st.get("id") or "")
                    except Exception:
                        open_pos["stop_order_id"] = None
                conn.execute(
                    """
                    UPDATE r6_paper_positions
                    SET qty=?, stop_order_id=?, updated_at=?, last_bar_ts=?
                    WHERE position_id=? AND status='OPEN'
                    """,
                    (
                        int(residual_qty),
                        (str(open_pos.get("stop_order_id") or "") or None),
                        datetime.now(CST).isoformat(),
                        str(exit_ts),
                        str(open_pos["position_id"]),
                    ),
                )
                conn.commit()
            _log_event(
                conn,
                "WARN",
                "exit_partial_unfilled",
                f"{symbol} exit not fully filled; position kept OPEN",
                symbol=symbol,
                data={
                    "exit_reason": exit_reason,
                    "position_id": str(open_pos["position_id"]),
                    "broker_side": str((broker_pos or {}).get("side") or ""),
                    "broker_qty": str((broker_pos or {}).get("qty") or ""),
                },
                discord_webhook=discord_webhook,
            )
            return False

    pnl_per_share = (exit_price - entry_price) if side == "LONG" else (entry_price - exit_price)
    pnl = pnl_per_share * qty
    pnl_pct = (pnl_per_share / entry_price) if entry_price > 0 else 0.0
    r_mult = _r_at_price(side, entry_price, exit_price, risk) if risk > 0 else 0.0
    now_cst = datetime.now(CST).isoformat()

    conn.execute(
        """
        UPDATE r6_paper_positions
        SET status='CLOSED', updated_at=?, trail_price=?, last_bar_ts=?, stop_order_id=NULL
        WHERE position_id=? AND status='OPEN'
        """,
        (now_cst, float(open_pos["trail_price"]), str(exit_ts), str(open_pos["position_id"])),
    )
    conn.execute(
        """
        INSERT INTO r6_paper_trades (
            trade_id, position_id, symbol, strategy_id, session_date, side, qty,
            entry_ts, exit_ts, entry_price, exit_price, stop_price, risk,
            pnl, pnl_pct, r_mult, exit_reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            str(open_pos["position_id"]),
            symbol,
            str(open_pos["strategy_id"]),
            str(open_pos["session_date"]),
            side,
            qty,
            str(open_pos["entry_ts"]),
            str(exit_ts),
            entry_price,
            float(exit_price),
            float(open_pos["stop_price"]),
            risk,
            pnl,
            pnl_pct,
            r_mult,
            exit_reason,
            now_cst,
        ),
    )
    conn.commit()
    _log_event(
        conn,
        "INFO",
        "position_closed",
        f"Closed {symbol} {side} reason={exit_reason}",
        symbol=symbol,
        data={"exit_price": float(exit_price), "pnl": float(pnl), "r_mult": float(r_mult)},
        discord_webhook=None,
    )
    _notify_trade(
        discord_webhook,
        "position_closed",
        symbol,
        {
            "side": side,
            "strategy_id": str(open_pos["strategy_id"]),
            "qty": int(qty),
            "entry_ts": str(open_pos["entry_ts"]),
            "entry_price": float(entry_price),
            "stop_price": float(open_pos["stop_price"]),
            "risk": round(float(risk), 4),
            "exit_price": float(exit_price),
            "reason": exit_reason,
            "pnl": round(float(pnl), 2),
            "r_mult": round(float(r_mult), 4),
            "position_id": str(open_pos["position_id"]),
            "exit_strategy": _exit_strategy_text(str(open_pos["strategy_id"]), time_exit_hhmm),
            "time_exit_ct": (time_exit_hhmm or "-"),
        },
    )
    return True


def _force_session_failsafe(
    conn: sqlite3.Connection,
    broker: AlpacaTradingClient | None,
    *,
    symbols: list[str],
    interval: str,
    session_date: str,
    reason: str,
    dry_run: bool,
    discord_webhook: str | None,
    time_exit_hhmm: str | None,
    cancel_open_orders: bool = True,
) -> dict:
    open_positions = _fetch_open_positions(conn, session_date=session_date)
    closed_positions = 0
    close_errors = 0
    canceled_orders = 0
    symbols_closed: set[str] = set()

    for open_pos in open_positions:
        symbol = str(open_pos["symbol"])
        side = str(open_pos["side"])
        exit_price = _estimate_symbol_exit_price(
            conn,
            broker,
            symbol=symbol,
            interval=interval,
            session_date=session_date,
            fallback_price=float(open_pos.get("entry_price") or 0.0),
        )
        send_broker_exit = symbol not in symbols_closed
        try:
            closed_ok = _close_position(
                conn,
                broker,
                open_pos,
                exit_price,
                datetime.now(CST).isoformat(),
                reason,
                dry_run,
                discord_webhook,
                send_broker_exit=send_broker_exit,
                time_exit_hhmm=time_exit_hhmm,
            )
            if closed_ok:
                closed_positions += 1
            else:
                close_errors += 1
        except Exception as exc:
            close_errors += 1
            _log_event(
                conn,
                "ERROR",
                "failsafe_close_failed",
                f"Failsafe close failed for {symbol}",
                symbol=symbol,
                data={"reason": reason, "error": str(exc)[:1000]},
                discord_webhook=discord_webhook,
            )
        symbols_closed.add(symbol)

    if cancel_open_orders and (not dry_run) and broker is not None:
        sym_list = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
        for symbol in sym_list:
            try:
                open_orders = broker.list_open_orders(symbol=symbol) or []
            except Exception as exc:
                _log_event(
                    conn,
                    "WARN",
                    "failsafe_order_scan_failed",
                    f"Failsafe open-order scan failed for {symbol}",
                    symbol=symbol,
                    data={"reason": reason, "error": str(exc)[:1000]},
                    discord_webhook=discord_webhook,
                )
                continue
            for o in open_orders:
                oid = str(o.get("id") or "").strip()
                if not oid:
                    continue
                try:
                    broker.cancel_order(oid)
                    canceled_orders += 1
                except Exception as exc:
                    _log_event(
                        conn,
                        "WARN",
                        "failsafe_order_cancel_failed",
                        f"Failsafe cancel failed for {symbol} order {oid}",
                        symbol=symbol,
                        data={"reason": reason, "error": str(exc)[:1000]},
                        discord_webhook=discord_webhook,
                    )

        # Final residual flatten sweep for partial exits near session close.
        for symbol in sym_list:
            if _attempt_force_flatten_symbol(broker, symbol=symbol, attempts=3, sleep_seconds=0.75):
                continue
            close_errors += 1
            broker_pos = _broker_position_for_symbol(broker, symbol)
            _log_event(
                conn,
                "ERROR",
                "failsafe_residual_position",
                f"Residual broker position remains for {symbol} after failsafe",
                symbol=symbol,
                data={
                    "reason": reason,
                    "broker_side": str((broker_pos or {}).get("side") or ""),
                    "broker_qty": str((broker_pos or {}).get("qty") or ""),
                },
                discord_webhook=discord_webhook,
            )

    if closed_positions > 0 or canceled_orders > 0 or close_errors > 0:
        _log_event(
            conn,
            "INFO" if close_errors == 0 else "WARN",
            "session_failsafe_summary",
            f"R6 failsafe {reason}: closed={closed_positions}, canceled_orders={canceled_orders}, errors={close_errors}",
            data={
                "session_date": session_date,
                "reason": reason,
                "closed_positions": int(closed_positions),
                "canceled_orders": int(canceled_orders),
                "close_errors": int(close_errors),
            },
            discord_webhook=None,
        )

    return {
        "session_date": session_date,
        "reason": reason,
        "closed_positions": int(closed_positions),
        "canceled_orders": int(canceled_orders),
        "close_errors": int(close_errors),
    }


def _publish_eod_trade_report(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    discord_webhook: str | None,
) -> dict:
    out_dir = Path("artifacts/reports/r6_paper")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"r6_eod_trades_{session_date}.csv"

    trades = pd.read_sql_query(
        """
        SELECT
            t.session_date, t.symbol, t.strategy_id, t.side, t.qty,
            t.entry_ts, t.exit_ts,
            t.entry_price, t.exit_price, t.stop_price, t.risk,
            t.pnl, t.pnl_pct, t.r_mult, t.exit_reason,
            p.confidence, p.primary_horizon, p.include_today_or, p.data_provider
        FROM r6_paper_trades t
        LEFT JOIN r6_paper_positions p ON p.position_id = t.position_id
        WHERE t.session_date = ?
        ORDER BY t.exit_ts, t.symbol
        """,
        conn,
        params=[session_date],
    )

    if trades.empty:
        report = pd.DataFrame(
            [
                {
                    "session_date": session_date,
                    "symbol": "TOTAL",
                    "strategy_id": "DAY_SUMMARY",
                    "side": "-",
                    "qty": 0,
                    "entry_ts_ct": "",
                    "exit_ts_ct": "",
                    "entry_price": 0.0,
                    "exit_price": 0.0,
                    "stop_price": 0.0,
                    "risk": 0.0,
                    "pnl": 0.0,
                    "pnl_pct": 0.0,
                    "r_mult": 0.0,
                    "exit_reason": "NO_TRADES",
                    "confidence": "",
                    "primary_horizon": "",
                    "include_today_or": "",
                    "data_provider": "",
                    "day_trades": 0,
                    "day_wins": 0,
                    "day_win_rate": 0.0,
                    "day_total_pnl": 0.0,
                    "day_avg_r": 0.0,
                }
            ]
        )
    else:
        work = trades.copy()
        for col in ("entry_ts", "exit_ts"):
            dt = pd.to_datetime(work[col], utc=True, errors="coerce")
            work[f"{col}_ct"] = dt.dt.tz_convert(CST).dt.strftime("%Y-%m-%d %H:%M:%S%z")

        n = int(len(work))
        wins = int((pd.to_numeric(work["pnl"], errors="coerce").fillna(0.0) > 0).sum())
        day_total_pnl = float(pd.to_numeric(work["pnl"], errors="coerce").fillna(0.0).sum())
        day_avg_r = float(pd.to_numeric(work["r_mult"], errors="coerce").fillna(0.0).mean())
        day_win_rate = float((wins / n) if n > 0 else 0.0)

        work["day_trades"] = ""
        work["day_wins"] = ""
        work["day_win_rate"] = ""
        work["day_total_pnl"] = ""
        work["day_avg_r"] = ""
        keep_cols = [
            "session_date",
            "symbol",
            "strategy_id",
            "side",
            "qty",
            "entry_ts_ct",
            "exit_ts_ct",
            "entry_price",
            "exit_price",
            "stop_price",
            "risk",
            "pnl",
            "pnl_pct",
            "r_mult",
            "exit_reason",
            "confidence",
            "primary_horizon",
            "include_today_or",
            "data_provider",
            "day_trades",
            "day_wins",
            "day_win_rate",
            "day_total_pnl",
            "day_avg_r",
        ]
        report = work[keep_cols].copy()
        summary_row = {k: "" for k in keep_cols}
        summary_row.update(
            {
                "session_date": session_date,
                "symbol": "TOTAL",
                "strategy_id": "DAY_SUMMARY",
                "side": "-",
                "qty": int(pd.to_numeric(work["qty"], errors="coerce").fillna(0).sum()),
                "pnl": day_total_pnl,
                "pnl_pct": float(pd.to_numeric(work["pnl_pct"], errors="coerce").fillna(0.0).mean()) if n > 0 else 0.0,
                "r_mult": day_avg_r,
                "exit_reason": "SUMMARY",
                "day_trades": n,
                "day_wins": wins,
                "day_win_rate": day_win_rate,
                "day_total_pnl": day_total_pnl,
                "day_avg_r": day_avg_r,
            }
        )
        report = pd.concat([report, pd.DataFrame([summary_row])], ignore_index=True)

    report.to_csv(out_path, index=False)

    posted = False
    if discord_webhook:
        content = f"📊 R6 EOD trade report {session_date}"
        try:
            posted = send_discord_file(
                discord_webhook,
                content,
                out_path.name,
                out_path.read_bytes(),
            )
        except Exception:
            posted = False

    _log_event(
        conn,
        "INFO",
        "eod_trade_report",
        f"R6 EOD trade report generated for {session_date}",
        data={"path": str(out_path), "posted_to_discord": bool(posted)},
        discord_webhook=None,
    )
    return {"path": str(out_path), "posted_to_discord": bool(posted)}


def _replace_stop_order(
    broker: AlpacaTradingClient | None,
    open_pos: dict,
    *,
    side: str,
    new_stop: float,
) -> str | None:
    if broker is None:
        return None
    current_stop_id = str(open_pos.get("stop_order_id") or "")
    if current_stop_id:
        try:
            broker.cancel_order(current_stop_id)
        except Exception:
            pass
    stop_side = "sell" if side == "LONG" else "buy"
    stop_oid = f"r6-stop-update-{open_pos['symbol']}-{uuid.uuid4().hex[:16]}"
    order = broker.submit_stop_order(
        symbol=str(open_pos["symbol"]),
        side=stop_side,
        qty=int(open_pos["qty"]),
        stop_price=float(new_stop),
        client_order_id=stop_oid,
    )
    return str(order.get("id") or "")


def _manage_open_position_for_bar(
    conn: sqlite3.Connection,
    broker: AlpacaTradingClient | None,
    open_pos: dict,
    bar: pd.Series,
    time_exit_hhmm: str,
    dry_run: bool,
    discord_webhook: str | None,
) -> dict:
    side = str(open_pos["side"])
    trail = float(open_pos["trail_price"])
    bar_open = float(bar["open"])
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    ema_prev = float(bar["ema20_prev"])
    bar_close_ts = str(bar["close_ts_cst"])
    bar_close_ct = pd.to_datetime(bar_close_ts).tz_convert(CST).time()
    eh, em = [int(x) for x in time_exit_hhmm.split(":")]

    if side == "LONG":
        trail = max(trail, ema_prev)
        stop_hit = low <= trail
    else:
        trail = min(trail, ema_prev)
        stop_hit = high >= trail

    if stop_hit:
        stop_fill = _stop_fill_price(side, trail, bar_open)
        closed_ok = _close_position(
            conn,
            broker,
            open_pos,
            float(stop_fill),
            bar_close_ts,
            "TRAIL_EMA_STOP",
            dry_run,
            discord_webhook,
            time_exit_hhmm=time_exit_hhmm,
        )
        return {"closed": bool(closed_ok)}

    if (bar_close_ct.hour, bar_close_ct.minute) >= (eh, em):
        closed_ok = _close_position(
            conn,
            broker,
            open_pos,
            close,
            bar_close_ts,
            "EOD",
            dry_run,
            discord_webhook,
            time_exit_hhmm=time_exit_hhmm,
        )
        return {"closed": bool(closed_ok)}

    stop_order_id = str(open_pos.get("stop_order_id") or "")
    trail_changed = abs(float(trail) - float(open_pos["trail_price"])) >= 0.01
    if trail_changed and (not dry_run) and broker is not None:
        try:
            updated_stop_id = _replace_stop_order(broker, open_pos, side=side, new_stop=trail)
            if updated_stop_id:
                stop_order_id = updated_stop_id
        except Exception:
            pass

    conn.execute(
        """
        UPDATE r6_paper_positions
        SET trail_price=?, stop_price=?, stop_order_id=?, last_bar_ts=?, updated_at=?
        WHERE position_id=? AND status='OPEN'
        """,
        (
            trail,
            trail,
            (stop_order_id or None),
            bar_close_ts,
            datetime.now(CST).isoformat(),
            str(open_pos["position_id"]),
        ),
    )
    conn.commit()
    open_pos["stop_order_id"] = (stop_order_id or None)
    open_pos["stop_price"] = float(trail)
    open_pos["trail_price"] = float(trail)

    return {"closed": False}


def run(config_path: str = "orb_r6_config.yaml") -> None:
    cfg = load_config(config_path)
    conn = connect(cfg.db_path)
    init_db(conn)
    ensure_breakout_tables(conn)
    _ensure_paper_tables(conn)

    symbols = load_symbols(cfg.symbols)
    interval = cfg.market_data.interval
    or_minutes = int(cfg.market_data.opening_range_minutes)
    horizons = sorted(int(x) for x in cfg.market_data.lookback_days)
    paper_cfg = cfg.paper or {}

    strategy_id = str(paper_cfg.get("strategy_id", "R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY"))
    dry_run = bool(paper_cfg.get("dry_run", True))
    risk_pct = float(paper_cfg.get("risk_pct_per_trade", 0.005))
    max_notional_pct = float(paper_cfg.get("max_notional_pct", 0.20))
    max_notional_dollars = float(paper_cfg.get("max_notional_dollars", 5000.0))
    default_equity = float(paper_cfg.get("default_equity", 100000.0))
    poll_seconds = int(paper_cfg.get("poll_seconds", 10))
    stop_buffer = float(paper_cfg.get("stop_buffer", 0.01))
    short_requires_inventory = bool(paper_cfg.get("short_requires_inventory", True))
    confidence_sizing_enabled = bool(paper_cfg.get("confidence_sizing_enabled", False))
    confidence_floor = float(paper_cfg.get("confidence_floor", 0.62))
    confidence_full = float(paper_cfg.get("confidence_full", 0.85))
    confidence_min_multiplier = float(paper_cfg.get("confidence_min_multiplier", 0.50))
    entry_max_age_bars = max(1, int(paper_cfg.get("entry_max_age_bars", 1)))
    time_exit = str(paper_cfg.get("time_exit", cfg.session.end))
    interval_minutes = _interval_minutes(interval)
    dashboard_enabled = bool(paper_cfg.get("dashboard", True))
    dashboard_min_refresh_seconds = float(max(1, int(paper_cfg.get("dashboard_min_refresh_seconds", 30))))
    discord_cfg = cfg.discord or {}
    discord_enabled = bool(discord_cfg.get("enabled", False))
    discord_webhook = (discord_cfg.get("webhook_url") or "").strip() if discord_enabled else ""

    broker = None if dry_run else AlpacaTradingClient.from_env(env_prefix="R6")
    if not dry_run and broker is None:
        raise RuntimeError("Missing Alpaca credentials for r6_paper execution")

    now_cst = datetime.now(CST)
    session_date = now_cst.date().isoformat()
    if not _is_session_day(cfg.session.calendar, session_date):
        print(f"[R6_PAPER] {session_date} is not a session day; exiting.")
        conn.close()
        return

    session_start_dt = combine_cst_date_time(session_date, cfg.session.start)
    session_end_dt = combine_cst_date_time(session_date, cfg.session.end)
    or_end_dt = session_start_dt + timedelta(minutes=or_minutes)
    try:
        time_exit_dt = combine_cst_date_time(session_date, time_exit)
    except Exception:
        time_exit_dt = session_end_dt

    if now_cst < session_start_dt:
        print(f"[R6_PAPER] Waiting for session open {session_start_dt.isoformat()}")
        while datetime.now(CST) < session_start_dt:
            time.sleep(10)

    print("=" * 70)
    print("R6_PAPER: live bars + Alpaca paper execution")
    print(f"Config: {config_path}")
    print(f"Provider: {cfg.market_data.provider} (yahoo now, switch to alpaca later)")
    print(f"Dry run: {dry_run}")
    print(f"Strategy: {strategy_id}")
    print(f"Session: {cfg.session.start}-{cfg.session.end} CT | OR={or_minutes}m | TF={interval}")
    print(f"Symbols: {symbols}")
    print("=" * 70)

    ensure_asof_ready(conn, cfg, session_date)
    templates = load_templates((cfg.discord or {}).get("templates_path", "./agent/orb_r6/templates/discord_alerts.yaml"))
    last_close_by_sym: Dict[str, Optional[str]] = {s: None for s in symbols}
    state_by_sym_phase: Dict[str, Dict[int, Dict[int, HorizonState]]] = {s: {0: {}, 1: {}} for s in symbols}
    or_end_ts = pd.Timestamp(or_end_dt)
    for sym in symbols:
        for phase in (0, 1):
            broken = load_broken_horizons(conn, sym, session_date, interval, or_minutes, phase)
            for h in broken.keys():
                state_by_sym_phase[sym][phase][int(h)] = HorizonState(armed=False)

    refreshed_post_rr = False
    cycle_count = 0
    bars_processed = 0
    entries_opened = 0
    positions_closed = 0
    last_action = "init"
    last_bar_close = None
    open_positions_local = _count_open_positions(conn)
    open_positions_broker = _count_broker_open_positions(broker, dry_run)
    last_dashboard_signature: tuple | None = None
    last_dashboard_render_monotonic = 0.0
    forced_exit_done = False
    forced_exit_summary: dict | None = None
    eod_failsafe_summary: dict | None = None
    while True:
        cycle_count += 1
        now_cst = datetime.now(CST)
        if (not forced_exit_done) and now_cst >= time_exit_dt:
            forced_reason = f"SESSION_FAILSAFE_{time_exit.replace(':', '')}"
            forced_exit_summary = _force_session_failsafe(
                conn,
                broker,
                symbols=symbols,
                interval=interval,
                session_date=session_date,
                reason=forced_reason,
                dry_run=dry_run,
                discord_webhook=discord_webhook,
                time_exit_hhmm=time_exit,
                cancel_open_orders=True,
            )
            positions_closed += int((forced_exit_summary or {}).get("closed_positions", 0))
            forced_exit_done = True
            if int((forced_exit_summary or {}).get("closed_positions", 0)) > 0:
                last_action = f"failsafe:{forced_reason}"

        if now_cst > session_end_dt:
            print("[R6_PAPER] session end reached")
            break

        last_complete = _last_complete_close_ts(now_cst, interval, session_start_dt)
        if last_complete is None:
            time.sleep(poll_seconds)
            continue

        ingest_end = last_complete.to_pydatetime()
        if (not refreshed_post_rr) and (now_cst >= or_end_dt):
            ensure_asof_ready(conn, cfg, session_date)
            refreshed_post_rr = True

        for sym in symbols:
            try:
                run_ingest(
                    conn=conn,
                    symbol=sym,
                    interval=interval,
                    start_cst=session_start_dt,
                    end_cst=ingest_end,
                    session_dates_cst=[session_date],
                    session_start=cfg.session.start,
                    session_end=cfg.session.end,
                    provider=cfg.market_data.provider,
                    alpaca_feed=cfg.market_data.alpaca_feed,
                )
            except Exception as exc:
                _log_event(
                    conn,
                    "WARN",
                    "ingest_failed",
                    f"{sym} ingest failed: {exc}",
                    symbol=sym,
                    discord_webhook=discord_webhook,
                )
                continue

            rr_pre = load_rr_rows(conn, sym, session_date, or_minutes, interval, include_today_or=0)
            rr_post = load_rr_rows(conn, sym, session_date, or_minutes, interval, include_today_or=1)
            rr_seed = rr_pre or rr_post
            if not rr_seed:
                continue

            day = _load_day_candles_for_symbol(conn, sym, interval, session_date)
            if day.empty:
                continue
            if last_close_by_sym[sym]:
                day_new = day[pd.to_datetime(day["close_ts_cst"], utc=True) > pd.to_datetime(last_close_by_sym[sym], utc=True)].copy()
            else:
                day_new = day.copy()
            day_new = day_new[pd.to_datetime(day_new["close_ts_cst"], utc=True) <= last_complete].copy()
            if day_new.empty:
                continue

            open_pos = _fetch_open_position(conn, sym)
            broker_pos = None if dry_run else _broker_position_for_symbol(broker, sym)
            if open_pos is None and broker_pos is not None:
                last_action = f"{sym}:broker_untracked_position"
                _log_event(
                    conn,
                    "WARN",
                    "broker_untracked_position",
                    f"{sym} has broker open position but no DB open row; skipping new entries",
                    symbol=sym,
                    data={
                        "broker_side": str(broker_pos.get("side") or ""),
                        "broker_qty": str(broker_pos.get("qty") or ""),
                    },
                    discord_webhook=discord_webhook,
                )
                last_close_by_sym[sym] = str(day_new.iloc[-1]["close_ts_cst"]) if not day_new.empty else last_close_by_sym[sym]
                continue
            for _, row in day_new.iterrows():
                bars_processed += 1
                last_bar_close = str(row["close_ts_cst"])
                if open_pos is not None:
                    if (not dry_run) and broker is not None:
                        broker_live = _broker_position_for_symbol(broker, sym)
                        if broker_live is None:
                            closed_ok = _close_position(
                                conn,
                                broker,
                                open_pos,
                                float(row["close"]),
                                str(row["close_ts_cst"]),
                                "BROKER_SYNC_FLAT",
                                dry_run,
                                discord_webhook,
                                send_broker_exit=False,
                            )
                            if closed_ok:
                                positions_closed += 1
                                last_action = f"{sym}:broker_sync_flat"
                                open_pos = None
                        else:
                            broker_signed = _signed_qty_from_position(broker_live)
                            expected_sign = 1 if str(open_pos["side"]) == "LONG" else -1
                            if broker_signed == 0 or (1 if broker_signed > 0 else -1) != expected_sign:
                                closed_ok = _close_position(
                                    conn,
                                    broker,
                                    open_pos,
                                    float(row["close"]),
                                    str(row["close_ts_cst"]),
                                    "BROKER_SYNC_MISMATCH",
                                    dry_run,
                                    discord_webhook,
                                    send_broker_exit=False,
                                )
                                if closed_ok:
                                    positions_closed += 1
                                    last_action = f"{sym}:broker_sync_mismatch"
                                    open_pos = None
                            else:
                                broker_qty = abs(int(broker_signed))
                                db_qty = int(open_pos["qty"])
                                if broker_qty != db_qty:
                                    conn.execute(
                                        """
                                        UPDATE r6_paper_positions
                                        SET qty=?, updated_at=?
                                        WHERE position_id=? AND status='OPEN'
                                        """,
                                        (
                                            int(broker_qty),
                                            datetime.now(CST).isoformat(),
                                            str(open_pos["position_id"]),
                                        ),
                                    )
                                    conn.commit()
                                    open_pos["qty"] = int(broker_qty)
                                    _log_event(
                                        conn,
                                        "WARN",
                                        "broker_qty_sync",
                                        f"{sym} qty synced from DB {db_qty} to broker {broker_qty}",
                                        symbol=sym,
                                        data={"db_qty": int(db_qty), "broker_qty": int(broker_qty)},
                                        discord_webhook=None,
                                    )
                    if open_pos is None:
                        continue
                    managed = _manage_open_position_for_bar(conn, broker, open_pos, row, time_exit, dry_run, discord_webhook)
                    if managed["closed"]:
                        positions_closed += 1
                        last_action = f"{sym}:position_closed"
                        open_pos = None
                    else:
                        open_pos = _fetch_open_position(conn, sym)

                res = evaluate_bar_close_only(
                    conn=conn,
                    templates=templates,
                    discord_enabled=discord_enabled,
                    webhook=discord_webhook,
                    tag="",
                    mode="PAPER",
                    symbol=sym,
                    asof_date_cst=session_date,
                    interval=interval,
                    or_minutes=or_minutes,
                    horizons=horizons,
                    rr_pre=rr_pre,
                    rr_post=rr_post,
                    rr_seed=rr_seed,
                    state_by_h=state_by_sym_phase[sym][0 if pd.to_datetime(row["close_ts_cst"]) < or_end_ts else 1],
                    row=row,
                    or_end_ts_cst=or_end_ts,
                )
                if res.event_id is not None and open_pos is None:
                    try:
                        opened = _try_enter_from_event(
                            conn,
                            broker,
                            event_id=str(res.event_id),
                            symbol=sym,
                            session_date=session_date,
                            strategy_id=strategy_id,
                            dry_run=dry_run,
                            risk_pct=risk_pct,
                            max_notional_pct=max_notional_pct,
                            max_notional_dollars=max_notional_dollars,
                            default_equity=default_equity,
                            stop_buffer=stop_buffer,
                            short_requires_inventory=short_requires_inventory,
                            confidence_sizing_enabled=confidence_sizing_enabled,
                            confidence_floor=confidence_floor,
                            confidence_full=confidence_full,
                            confidence_min_multiplier=confidence_min_multiplier,
                            provider=cfg.market_data.provider,
                            discord_webhook=discord_webhook,
                            time_exit_hhmm=time_exit,
                            latest_complete_utc=last_complete,
                            interval_minutes=interval_minutes,
                            entry_max_age_bars=entry_max_age_bars,
                        )
                    except Exception as exc:
                        opened = False
                        _log_event(
                            conn,
                            "ERROR",
                            "entry_submit_failed",
                            f"{sym} entry submit failed: {exc}",
                            symbol=sym,
                            discord_webhook=discord_webhook,
                        )
                    if opened:
                        entries_opened += 1
                        last_action = f"{sym}:entry_opened"
                    open_pos = _fetch_open_position(conn, sym)

                last_close_by_sym[sym] = str(row["close_ts_cst"])

        open_positions_local = _count_open_positions(conn)
        open_positions_broker = _count_broker_open_positions(broker, dry_run)
        dashboard_signature = (
            bars_processed,
            entries_opened,
            positions_closed,
            open_positions_local,
            open_positions_broker,
            last_bar_close,
            last_action,
        )
        now_mono = time.monotonic()
        refresh_elapsed = now_mono - last_dashboard_render_monotonic
        should_render = (
            dashboard_signature != last_dashboard_signature
            or refresh_elapsed >= dashboard_min_refresh_seconds
            or last_dashboard_signature is None
        )
        if should_render:
            _render_dashboard(
                enabled=dashboard_enabled,
                cfg_path=config_path,
                strategy_id=strategy_id,
                session_date=session_date,
                dry_run=dry_run,
                provider=cfg.market_data.provider,
                symbols=symbols,
                cycle_count=cycle_count,
                bars_processed=bars_processed,
                entries_opened=entries_opened,
                positions_closed=positions_closed,
                open_positions_local=open_positions_local,
                open_positions_broker=open_positions_broker,
                last_bar_close=last_bar_close,
                last_action=last_action,
            )
            last_dashboard_signature = dashboard_signature
            last_dashboard_render_monotonic = now_mono
        time.sleep(poll_seconds)

    eod_failsafe_summary = _force_session_failsafe(
        conn,
        broker,
        symbols=symbols,
        interval=interval,
        session_date=session_date,
        reason="SESSION_FAILSAFE_EOD",
        dry_run=dry_run,
        discord_webhook=discord_webhook,
        time_exit_hhmm=time_exit,
        cancel_open_orders=True,
    )
    positions_closed += int((eod_failsafe_summary or {}).get("closed_positions", 0))
    print(
        f"[R6_PAPER] failsafe summary: forced={forced_exit_summary or {'closed_positions': 0, 'canceled_orders': 0, 'close_errors': 0}} "
        f"| eod={eod_failsafe_summary or {'closed_positions': 0, 'canceled_orders': 0, 'close_errors': 0}}"
    )
    eod_trade_report = _publish_eod_trade_report(
        conn,
        session_date=session_date,
        discord_webhook=discord_webhook,
    )
    print(
        f"[R6_PAPER] eod trade report: path={eod_trade_report.get('path')} "
        f"| posted_to_discord={bool(eod_trade_report.get('posted_to_discord'))}"
    )
    conn.close()


if __name__ == "__main__":
    run()
