from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any

import pandas as pd
import pandas_market_calendars as mcal
import requests
import yaml
from zoneinfo import ZoneInfo


CST = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class BrokerClient:
    api_key: str
    secret_key: str
    base_url: str = "https://paper-api.alpaca.markets/v2"

    @classmethod
    def from_env(cls) -> "BrokerClient | None":
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        base_url = os.getenv("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets/v2"
        if not api_key or not secret_key:
            return None
        return cls(api_key=api_key, secret_key=secret_key, base_url=base_url.rstrip("/"))

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }
        resp = requests.request(method=method, url=url, headers=headers, params=params, json=payload, timeout=30)
        if resp.status_code >= 400:
            raise requests.HTTPError(f"Alpaca API error {resp.status_code}: {resp.text}", response=resp)
        if not resp.text.strip():
            return {}
        return resp.json()

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/account")

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/positions/{symbol}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def list_positions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/positions")

    def list_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"status": "open"}
        if symbol:
            params["symbols"] = symbol
        return self._request("GET", "/orders", params=params)

    def submit_market_order(self, symbol: str, side: str, qty: int, client_order_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/orders",
            payload={
                "symbol": symbol,
                "side": side.lower(),
                "type": "market",
                "time_in_force": "day",
                "qty": str(int(qty)),
                "client_order_id": client_order_id,
            },
        )

    def submit_stop_order(self, symbol: str, side: str, qty: int, stop_price: float, client_order_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/orders",
            payload={
                "symbol": symbol,
                "side": side.lower(),
                "type": "stop",
                "time_in_force": "day",
                "qty": str(int(qty)),
                "stop_price": f"{float(stop_price):.2f}",
                "client_order_id": client_order_id,
            },
        )

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/orders/{order_id}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-agent execution router (single broker writer)")
    p.add_argument("--config", default="multi_agent_router/config.yaml")
    return p.parse_args()


def load_cfg(path: str) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return cfg


def connect_sqlite(path: str) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def ensure_state_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS consumed_signals (
            signal_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_ts TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT,
            processed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            confidence REAL,
            weight REAL NOT NULL,
            score REAL NOT NULL,
            signal_ts TEXT NOT NULL,
            window_start_ts TEXT NOT NULL,
            window_end_ts TEXT NOT NULL,
            strategy_id TEXT,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            risk REAL NOT NULL,
            payload_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS managed_positions (
            position_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            strategy_id TEXT,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_ts TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            risk REAL NOT NULL,
            stop_order_id TEXT,
            status TEXT NOT NULL,
            exit_ts TEXT,
            exit_price REAL,
            exit_reason TEXT,
            data_json TEXT
        )
        """
    )
    conn.execute("DROP INDEX IF EXISTS ux_managed_symbol_open")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_managed_symbol_open_only
        ON managed_positions(symbol)
        WHERE status='OPEN'
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            symbol TEXT,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            data_json TEXT
        )
        """
    )
    conn.commit()


def now_ct() -> datetime:
    return datetime.now(CST)


def ts_iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_ts(s: str) -> datetime:
    return pd.to_datetime(s, utc=True).to_pydatetime()


def is_session_day(calendar_name: str, session_date: str) -> bool:
    cal = mcal.get_calendar(calendar_name)
    sched = cal.schedule(start_date=session_date, end_date=session_date)
    return not sched.empty


def log_event(conn: sqlite3.Connection, level: str, event_type: str, message: str, symbol: str | None = None, data: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO execution_events(ts, level, symbol, event_type, message, data_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ts_iso(now_ct()),
            level,
            symbol,
            event_type,
            message,
            json.dumps(data or {}, sort_keys=True),
        ),
    )
    conn.commit()


def fetch_orb_signals(cfg: dict[str, Any], session_date: str) -> list[dict]:
    src = cfg["sources"]["orb"]
    if not src.get("enabled", True):
        return []
    conn = sqlite3.connect(src["db_path"])
    try:
        rows = conn.execute(
            """
            SELECT event_id, created_at, symbol, data_json
            FROM live_events
            WHERE event_type='entry_signal_detected'
            ORDER BY created_at ASC
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for event_id, _, symbol, data_json in rows:
        d = json.loads(data_json or "{}")
        signal_ts = str(d.get("entry_ts") or "")
        if not signal_ts:
            continue
        dt = parse_ts(signal_ts)
        if dt.astimezone(CST).date().isoformat() != session_date:
            continue
        side = str(d.get("side") or "").upper()
        if side not in {"LONG", "SHORT"}:
            continue
        entry = float(d.get("entry_price") or 0.0)
        stop = float(d.get("stop_price") or 0.0)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        conf = float(src.get("default_confidence", 0.72))
        out.append(
            {
                "signal_key": f"ORB:{event_id}",
                "source": "ORB",
                "source_event_id": str(event_id),
                "symbol": str(symbol).upper(),
                "side": side,
                "signal_ts": dt,
                "confidence": conf,
                "weight": float(src.get("weight", 1.0)),
                "strategy_id": str(d.get("strategy_id") or ""),
                "entry_price": entry,
                "stop_price": stop,
                "risk": risk,
                "payload": d,
            }
        )
    return out


def fetch_r6_signals(cfg: dict[str, Any], session_date: str) -> list[dict]:
    src = cfg["sources"]["r6"]
    if not src.get("enabled", True):
        return []
    conn = sqlite3.connect(src["db_path"])
    try:
        rows = conn.execute(
            """
            SELECT event_id, symbol, bar_close_ts_cst, decision, confidence,
                   candle_close, candle_low, candle_high, ref_high, ref_low, ref_width, primary_horizon
            FROM breakout_events
            WHERE decision IN ('LONG','SHORT')
            ORDER BY bar_close_ts_cst ASC
            """
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    stop_buffer = float(cfg["execution"].get("r6_stop_buffer", 0.01))
    for event_id, symbol, bar_ts, decision, conf, candle_close, candle_low, candle_high, ref_high, ref_low, ref_width, ph in rows:
        dt = pd.to_datetime(bar_ts).tz_convert(UTC).to_pydatetime()
        if dt.astimezone(CST).date().isoformat() != session_date:
            continue
        side = str(decision).upper()
        entry = float(candle_close or 0.0)
        if side == "LONG":
            stop = min(float(candle_low) - stop_buffer, entry - 0.01)
        else:
            stop = max(float(candle_high) + stop_buffer, entry + 0.01)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        d = {
            "ref_high": ref_high,
            "ref_low": ref_low,
            "ref_width": ref_width,
            "primary_horizon": ph,
        }
        out.append(
            {
                "signal_key": f"R6:{event_id}",
                "source": "R6",
                "source_event_id": str(event_id),
                "symbol": str(symbol).upper(),
                "side": side,
                "signal_ts": dt,
                "confidence": float(conf or 0.0),
                "weight": float(src.get("weight", 1.0)),
                "strategy_id": "R6",
                "entry_price": entry,
                "stop_price": stop,
                "risk": risk,
                "payload": d,
            }
        )
    return out


def already_processed_or_pending(conn: sqlite3.Connection, signal_key: str) -> bool:
    r1 = conn.execute("SELECT 1 FROM consumed_signals WHERE signal_key=? LIMIT 1", (signal_key,)).fetchone()
    if r1:
        return True
    r2 = conn.execute("SELECT 1 FROM pending_candidates WHERE signal_key=? LIMIT 1", (signal_key,)).fetchone()
    return r2 is not None


def queue_signals(conn: sqlite3.Connection, cfg: dict[str, Any], signals: list[dict], now: datetime) -> int:
    queued = 0
    mode = str(cfg["execution"].get("arbitration_mode", "score_window")).strip().lower()
    window_sec = int(cfg["execution"].get("decision_window_seconds", 180))
    max_age_sec = int(cfg["execution"].get("max_signal_age_seconds", 600))
    for s in sorted(signals, key=lambda x: x["signal_ts"]):
        if already_processed_or_pending(conn, s["signal_key"]):
            continue
        age = (now.astimezone(UTC) - s["signal_ts"]).total_seconds()
        if age > max_age_sec:
            _mark_consumed(
                conn,
                s["signal_key"],
                s["source"],
                s["source_event_id"],
                s["symbol"],
                ts_iso(s["signal_ts"]),
                "skipped",
                f"stale_signal_age={int(age)}s",
            )
            conn.commit()
            continue
        existing = conn.execute(
            "SELECT window_start_ts, window_end_ts FROM pending_candidates WHERE symbol=? ORDER BY id ASC LIMIT 1",
            (s["symbol"],),
        ).fetchone()
        if mode == "first_signal_wins" and existing:
            _mark_consumed(
                conn,
                s["signal_key"],
                s["source"],
                s["source_event_id"],
                s["symbol"],
                ts_iso(s["signal_ts"]),
                "skipped",
                "later_signal_after_first",
            )
            conn.commit()
            continue
        if existing:
            window_start = parse_ts(existing[0])
            window_end = parse_ts(existing[1])
        else:
            window_start = s["signal_ts"]
            window_end = s["signal_ts"] if mode == "first_signal_wins" else (s["signal_ts"] + timedelta(seconds=window_sec))
        conf = float(s["confidence"])
        score = float(s["weight"]) * conf
        conn.execute(
            """
            INSERT INTO pending_candidates
            (signal_key, source, source_event_id, symbol, side, confidence, weight, score, signal_ts,
             window_start_ts, window_end_ts, strategy_id, entry_price, stop_price, risk, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                s["signal_key"],
                s["source"],
                s["source_event_id"],
                s["symbol"],
                s["side"],
                conf,
                float(s["weight"]),
                score,
                ts_iso(s["signal_ts"]),
                ts_iso(window_start),
                ts_iso(window_end),
                s["strategy_id"],
                float(s["entry_price"]),
                float(s["stop_price"]),
                float(s["risk"]),
                json.dumps(s["payload"], sort_keys=True),
            ),
        )
        queued += 1
    conn.commit()
    return queued


def _cancel_open_orders_symbol(broker: BrokerClient, symbol: str) -> int:
    canceled = 0
    for o in broker.list_open_orders(symbol=symbol) or []:
        oid = str(o.get("id") or "")
        if not oid:
            continue
        try:
            broker.cancel_order(oid)
            canceled += 1
        except Exception:
            continue
    return canceled


def _available_long_qty(broker: BrokerClient, symbol: str) -> int:
    pos = broker.get_open_position(symbol)
    if not pos:
        return 0
    if str(pos.get("side", "")).lower() != "long":
        return 0
    try:
        return max(0, int(abs(float(pos.get("qty", 0.0)))))
    except Exception:
        return 0


def _compute_qty(equity: float, risk_pct: float, max_notional_pct: float, max_notional_dollars: float, entry_price: float, stop_price: float) -> int:
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0 or entry_price <= 0:
        return 0
    qty_risk = math.floor(max(0.0, equity * risk_pct) / risk_per_share)
    qty_notional_pct = math.floor(max(0.0, equity * max_notional_pct) / entry_price)
    qty_notional_abs = math.floor(max(0.0, max_notional_dollars) / entry_price)
    return max(0, int(min(qty_risk, qty_notional_pct, qty_notional_abs)))


def _mark_consumed(conn: sqlite3.Connection, signal_key: str, source: str, source_event_id: str, symbol: str, signal_ts: str, status: str, note: str | None = None) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO consumed_signals(signal_key, source, source_event_id, symbol, signal_ts, status, note, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (signal_key, source, source_event_id, symbol, signal_ts, status, note, ts_iso(now_ct())),
    )


def _load_open_position(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM managed_positions WHERE symbol=? AND status='OPEN' LIMIT 1",
        (symbol,),
    ).fetchone()
    conn.row_factory = None
    return row


def _reconcile_broker_state(conn: sqlite3.Connection, broker: BrokerClient) -> dict[str, int]:
    now = now_ct().astimezone(UTC)
    conn.row_factory = sqlite3.Row
    managed = conn.execute("SELECT * FROM managed_positions WHERE status='OPEN'").fetchall()
    conn.row_factory = None
    managed_by_symbol = {str(r["symbol"]): r for r in managed}

    broker_positions = broker.list_positions() or []
    broker_by_symbol = {str(p.get("symbol") or ""): p for p in broker_positions if str(p.get("symbol") or "")}

    closed_sync = 0
    inserted_sync = 0

    # Managed says OPEN but broker is flat -> close in state.
    for symbol, row in managed_by_symbol.items():
        if symbol in broker_by_symbol:
            continue
        conn.execute(
            """
            UPDATE managed_positions
            SET status='CLOSED', exit_ts=?, exit_reason='BROKER_FLAT_SYNC', stop_order_id=NULL
            WHERE position_id=? AND status='OPEN'
            """,
            (ts_iso(now), str(row["position_id"])),
        )
        log_event(conn, "WARN", "broker_flat_sync", f"{symbol} marked CLOSED in router state (broker flat)", symbol=symbol)
        closed_sync += 1

    # Broker has position but router has no OPEN row -> insert synthetic OPEN row so it can be managed.
    for symbol, pos in broker_by_symbol.items():
        if symbol in managed_by_symbol:
            continue
        side = "LONG" if str(pos.get("side", "")).lower() == "long" else "SHORT"
        try:
            qty = max(0, int(abs(float(pos.get("qty", 0.0)))))
        except Exception:
            qty = 0
        if qty <= 0:
            continue
        try:
            entry_price = float(pos.get("avg_entry_price") or pos.get("current_price") or 0.0)
        except Exception:
            entry_price = 0.0
        if entry_price <= 0:
            continue
        open_orders = broker.list_open_orders(symbol=symbol) or []
        stop_order_id = None
        for o in open_orders:
            if str(o.get("type", "")).lower() == "stop":
                stop_order_id = str(o.get("id") or "") or None
                break
        risk = max(entry_price * 0.005, 0.01)
        conn.execute(
            """
            INSERT OR IGNORE INTO managed_positions
            (position_id, symbol, source, source_event_id, strategy_id, side, qty, entry_ts, entry_price,
             stop_price, risk, stop_order_id, status, data_json)
            VALUES (?, ?, 'BROKER_SYNC', 'BROKER_SYNC', 'BROKER_SYNC', ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            """,
            (
                str(uuid.uuid4()),
                symbol,
                side,
                int(qty),
                ts_iso(now),
                float(entry_price),
                float(entry_price),
                float(risk),
                stop_order_id,
                json.dumps({"note": "inserted_from_existing_broker_position"}, sort_keys=True),
            ),
        )
        log_event(conn, "WARN", "broker_position_sync", f"{symbol} inserted as OPEN in router state from broker", symbol=symbol)
        inserted_sync += 1

    conn.commit()
    return {"closed_sync": closed_sync, "inserted_sync": inserted_sync}


def finalize_ready_symbols(conn: sqlite3.Connection, broker: BrokerClient, cfg: dict[str, Any], now: datetime) -> int:
    mode = str(cfg["execution"].get("arbitration_mode", "score_window")).strip().lower()
    rows = conn.execute(
        """
        SELECT symbol, MIN(window_end_ts) AS window_end_ts
        FROM pending_candidates
        GROUP BY symbol
        """
    ).fetchall()
    finalized = 0
    for symbol, end_ts in rows:
        if parse_ts(end_ts) > now.astimezone(UTC):
            continue
        candidates = conn.execute(
            """
            SELECT id, signal_key, source, source_event_id, symbol, side, confidence, score, signal_ts,
                   strategy_id, entry_price, stop_price, risk, payload_json
            FROM pending_candidates
            WHERE symbol=?
            ORDER BY score DESC, signal_ts ASC
            """,
            (symbol,),
        ).fetchall()
        if not candidates:
            continue
        if mode == "first_signal_wins":
            chosen = sorted(candidates, key=lambda c: (parse_ts(c[8]), -float(c[7])))[0]
        else:
            chosen = candidates[0]
        symbol = str(chosen[4])
        open_row = _load_open_position(conn, symbol)
        if open_row is not None:
            for c in candidates:
                _mark_consumed(conn, c[1], c[2], c[3], c[4], c[8], "skipped", "position_open")
            conn.execute("DELETE FROM pending_candidates WHERE symbol=?", (symbol,))
            conn.commit()
            finalized += 1
            continue

        broker_pos = broker.get_open_position(symbol)
        if broker_pos is not None:
            for c in candidates:
                _mark_consumed(conn, c[1], c[2], c[3], c[4], c[8], "skipped", "broker_position_exists")
            conn.execute("DELETE FROM pending_candidates WHERE symbol=?", (symbol,))
            log_event(conn, "WARN", "broker_position_exists", f"Skip {symbol}: broker already has open position", symbol=symbol)
            conn.commit()
            finalized += 1
            continue

        account = broker.get_account()
        equity = float(account.get("equity") or 0.0)
        entry_price = float(chosen[10])
        stop_price = float(chosen[11])
        qty = _compute_qty(
            equity=equity,
            risk_pct=float(cfg["execution"]["risk_pct_per_trade"]),
            max_notional_pct=float(cfg["execution"]["max_notional_pct"]),
            max_notional_dollars=float(cfg["execution"]["max_notional_dollars"]),
            entry_price=entry_price,
            stop_price=stop_price,
        )
        if qty <= 0:
            for c in candidates:
                _mark_consumed(conn, c[1], c[2], c[3], c[4], c[8], "skipped", "qty_zero")
            conn.execute("DELETE FROM pending_candidates WHERE symbol=?", (symbol,))
            conn.commit()
            finalized += 1
            continue

        side = str(chosen[5]).upper()
        if side == "SHORT" and bool(cfg["execution"].get("short_requires_inventory", True)):
            avail = _available_long_qty(broker, symbol)
            if avail < qty:
                for c in candidates:
                    _mark_consumed(conn, c[1], c[2], c[3], c[4], c[8], "skipped", "short_blocked_no_inventory")
                conn.execute("DELETE FROM pending_candidates WHERE symbol=?", (symbol,))
                log_event(
                    conn,
                    "WARN",
                    "short_blocked_no_inventory",
                    f"Skip SHORT {symbol}: required {qty}, available long qty {avail}",
                    symbol=symbol,
                    data={"required_qty": qty, "available_long_qty": avail},
                )
                conn.commit()
                finalized += 1
                continue

        _cancel_open_orders_symbol(broker, symbol)
        entry_side = "buy" if side == "LONG" else "sell"
        stop_side = "sell" if side == "LONG" else "buy"
        entry_oid = f"router-entry-{symbol}-{uuid.uuid4().hex[:14]}"
        stop_oid = f"router-stop-{symbol}-{uuid.uuid4().hex[:14]}"
        try:
            entry_order = broker.submit_market_order(symbol=symbol, side=entry_side, qty=qty, client_order_id=entry_oid)
            stop_order = broker.submit_stop_order(symbol=symbol, side=stop_side, qty=qty, stop_price=stop_price, client_order_id=stop_oid)
            position_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO managed_positions
                (position_id, symbol, source, source_event_id, strategy_id, side, qty, entry_ts, entry_price,
                 stop_price, risk, stop_order_id, status, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    position_id,
                    symbol,
                    str(chosen[2]),
                    str(chosen[3]),
                    str(chosen[9] or ""),
                    side,
                    int(qty),
                    ts_iso(now.astimezone(UTC)),
                    float(entry_price),
                    float(stop_price),
                    float(chosen[12]),
                    str(stop_order.get("id") or ""),
                    json.dumps(
                        {
                            "entry_order_id": str(entry_order.get("id") or ""),
                            "score": float(chosen[7]),
                            "confidence": float(chosen[6] or 0.0),
                        },
                        sort_keys=True,
                    ),
                ),
            )
            for c in candidates:
                st = "chosen" if c[0] == chosen[0] else "skipped"
                if c[0] == chosen[0]:
                    note = "winner"
                else:
                    note = "lost_arbitration_first_signal" if mode == "first_signal_wins" else "lost_arbitration"
                _mark_consumed(conn, c[1], c[2], c[3], c[4], c[8], st, note)
            conn.execute("DELETE FROM pending_candidates WHERE symbol=?", (symbol,))
            log_event(
                conn,
                "INFO",
                "entry_opened",
                f"Opened {side} {symbol} qty={qty} via {chosen[2]}",
                symbol=symbol,
                data={"qty": qty, "entry_price": entry_price, "stop_price": stop_price, "source": chosen[2], "strategy_id": chosen[9]},
            )
            conn.commit()
        except Exception as exc:
            for c in candidates:
                _mark_consumed(conn, c[1], c[2], c[3], c[4], c[8], "error", "entry_submit_failed")
            conn.execute("DELETE FROM pending_candidates WHERE symbol=?", (symbol,))
            log_event(conn, "ERROR", "entry_submit_failed", f"{symbol} entry failed: {exc}", symbol=symbol)
            conn.commit()
        finalized += 1
    return finalized


def manage_open_positions(conn: sqlite3.Connection, broker: BrokerClient, cfg: dict[str, Any], now: datetime) -> int:
    forced_hhmm = str(cfg["execution"].get("forced_exit_time_ct", "14:50"))
    hh, mm = [int(x) for x in forced_hhmm.split(":")]
    forced = dtime(hh, mm)
    if now.time() < forced:
        return 0
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM managed_positions WHERE status='OPEN'").fetchall()
    conn.row_factory = None
    closed = 0
    for r in rows:
        symbol = str(r["symbol"])
        side = str(r["side"])
        qty = int(r["qty"])
        try:
            stop_order_id = str(r["stop_order_id"] or "")
            if stop_order_id:
                try:
                    broker.cancel_order(stop_order_id)
                except Exception:
                    pass
            _cancel_open_orders_symbol(broker, symbol)
            exit_side = "sell" if side == "LONG" else "buy"
            exit_oid = f"router-exit-{symbol}-{uuid.uuid4().hex[:14]}"
            broker.submit_market_order(symbol=symbol, side=exit_side, qty=qty, client_order_id=exit_oid)
            conn.execute(
                """
                UPDATE managed_positions
                SET status='CLOSED', exit_ts=?, exit_reason='TIME_EXIT', stop_order_id=NULL
                WHERE position_id=?
                """,
                (ts_iso(now.astimezone(UTC)), str(r["position_id"])),
            )
            log_event(conn, "INFO", "position_closed", f"Closed {symbol} via TIME_EXIT", symbol=symbol)
            conn.commit()
            closed += 1
        except Exception as exc:
            log_event(conn, "ERROR", "close_failed", f"{symbol} close failed: {exc}", symbol=symbol)
    return closed


def render_dashboard(conn: sqlite3.Connection, cfg: dict[str, Any], last_action: str) -> None:
    now = now_ct()
    pending = conn.execute("SELECT COUNT(*) FROM pending_candidates").fetchone()[0]
    open_pos = conn.execute("SELECT COUNT(*) FROM managed_positions WHERE status='OPEN'").fetchone()[0]
    consumed_today = conn.execute(
        """
        SELECT COUNT(*)
        FROM consumed_signals
        WHERE DATE(processed_at) = DATE(?)
        """,
        (ts_iso(now.astimezone(UTC)),),
    ).fetchone()[0]
    recent = conn.execute(
        "SELECT ts, event_type, COALESCE(symbol,'-') FROM execution_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    recent_txt = "-" if not recent else f"{recent[0]} | {recent[1]} | {recent[2]}"
    arb_mode = str(cfg["execution"].get("arbitration_mode", "score_window")).strip().lower()
    print("\033[2J\033[H", end="")
    print("MULTI-AGENT ROUTER DASHBOARD")
    print("=" * 84)
    print(f"Now: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} | Session: {cfg['session']['start']}-{cfg['session']['end']} CT | Calendar: {cfg['session']['calendar']}")
    print(f"Mode: {'DRY' if cfg['execution'].get('dry_run', True) else 'LIVE'} | Arbitration: {arb_mode} | DecisionWindowSec: {cfg['execution']['decision_window_seconds']} | PollSec: {cfg['execution']['poll_seconds']}")
    print(f"Open Managed Positions: {open_pos} | Pending Candidates: {pending} | Consumed Signals Today: {consumed_today}")
    print(f"MaxNotional$: {cfg['execution']['max_notional_dollars']} | RiskPct: {cfg['execution']['risk_pct_per_trade']} | ShortNeedsInventory: {cfg['execution'].get('short_requires_inventory', True)}")
    print("-" * 84)
    print(f"Last Action: {last_action}")
    print(f"Recent Event: {recent_txt}")
    print("=" * 84)


def run() -> None:
    args = parse_args()
    cfg = load_cfg(args.config)
    state_conn = connect_sqlite(cfg["state_db"]["path"])
    ensure_state_schema(state_conn)

    dry_run = bool(cfg["execution"].get("dry_run", True))
    broker = None if dry_run else BrokerClient.from_env()
    if not dry_run and broker is None:
        raise RuntimeError("Missing Alpaca credentials for live router execution")
    if dry_run:
        print("[router] dry_run=true, broker execution disabled.")

    session_date = now_ct().date().isoformat()
    if not is_session_day(cfg["session"]["calendar"], session_date):
        print(f"[router] {session_date} is not a session day. Exiting.")
        return

    start_h, start_m = [int(x) for x in str(cfg["session"]["start"]).split(":")]
    end_h, end_m = [int(x) for x in str(cfg["session"]["end"]).split(":")]
    session_start = datetime.combine(now_ct().date(), dtime(start_h, start_m), tzinfo=CST)
    session_end = datetime.combine(now_ct().date(), dtime(end_h, end_m), tzinfo=CST)
    if now_ct() < session_start:
        print(f"[router] waiting for session open {session_start.isoformat()}")
        while now_ct() < session_start:
            time.sleep(5)

    last_action = "init"
    while True:
        now = now_ct()
        if now > session_end:
            break
        all_signals: list[dict] = []
        all_signals.extend(fetch_orb_signals(cfg, session_date))
        all_signals.extend(fetch_r6_signals(cfg, session_date))
        queued = queue_signals(state_conn, cfg, all_signals, now)
        if queued:
            last_action = f"queued={queued}"
        if not dry_run and broker is not None:
            rec = _reconcile_broker_state(state_conn, broker)
            finalized = finalize_ready_symbols(state_conn, broker, cfg, now)
            closed = manage_open_positions(state_conn, broker, cfg, now)
            if finalized or closed or rec["closed_sync"] or rec["inserted_sync"]:
                last_action = (
                    f"sync_closed={rec['closed_sync']} sync_inserted={rec['inserted_sync']} "
                    f"finalized={finalized} closed={closed}"
                )
        render_dashboard(state_conn, cfg, last_action)
        time.sleep(int(cfg["execution"].get("poll_seconds", 5)))

    print("[router] session complete.")


if __name__ == "__main__":
    run()
