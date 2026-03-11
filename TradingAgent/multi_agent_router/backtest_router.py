from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from zoneinfo import ZoneInfo


CST = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")


@dataclass
class Signal:
    signal_key: str
    source: str
    source_event_id: str
    symbol: str
    side: str
    signal_ts: datetime
    confidence: float
    weight: float
    strategy_id: str
    entry_price: float
    stop_price: float
    risk: float
    payload: dict[str, Any]


@dataclass
class OpenPosition:
    symbol: str
    side: str
    qty: int
    entry_ts: datetime
    entry_price: float
    stop_price: float
    risk: float
    exit_ts: datetime
    exit_price: float
    exit_reason: str
    source: str
    source_event_id: str
    strategy_id: str
    confidence: float
    score: float


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest multi-agent router arbitration policy")
    p.add_argument("--config", default="multi_agent_router/config.yaml")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--output-dir", default="artifacts/multi_agent_router/backtests")
    return p.parse_args()


def load_cfg(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def ts_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def load_bars(orb_db_path: str, symbols: list[str], start: str, end: str) -> pd.DataFrame:
    conn = sqlite3.connect(orb_db_path)
    try:
        ph = ",".join(["?"] * len(symbols))
        sql = f"SELECT symbol, ts, o, h, l, c, volume FROM bars_5m WHERE symbol IN ({ph})"
        bars = pd.read_sql_query(sql, conn, params=symbols)
    finally:
        conn.close()
    if bars.empty:
        return bars
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars["ts_ct"] = bars["ts"].dt.tz_convert(CST)
    bars["session_date"] = bars["ts_ct"].dt.strftime("%Y-%m-%d")
    bars = bars[(bars["session_date"] >= start) & (bars["session_date"] <= end)].copy()
    bars["time_ct"] = bars["ts_ct"].dt.time
    return bars.sort_values(["symbol", "ts"]).reset_index(drop=True)


def build_orb_signals(
    bars: pd.DataFrame,
    *,
    side_mode: str,
    orb_weight: float,
    orb_conf: float,
) -> list[Signal]:
    if bars.empty:
        return []
    out: list[Signal] = []
    session_start = dtime(8, 30)
    or_end = dtime(9, 0)
    last_entry = dtime(14, 45)
    by = bars.groupby(["symbol", "session_date"], sort=True)
    for (symbol, _), g in by:
        g = g.sort_values("ts").reset_index(drop=True)
        opening = g[(g["time_ct"] >= session_start) & (g["time_ct"] < or_end)]
        if len(opening) < 6:
            continue
        or_high = float(opening["h"].max())
        or_low = float(opening["l"].min())
        post = g[(g["time_ct"] >= or_end) & (g["time_ct"] <= last_entry)].reset_index(drop=True)
        if len(post) < 3:
            continue
        for i in range(2, len(post)):
            b2 = post.iloc[i - 2]
            b1 = post.iloc[i - 1]
            bc = post.iloc[i]
            long_confirm = bool(float(b1["c"]) > or_high and float(bc["c"]) > or_high and float(b2["c"]) <= or_high)
            short_confirm = bool(float(b1["c"]) < or_low and float(bc["c"]) < or_low and float(b2["c"]) >= or_low)
            if side_mode == "long_only":
                short_confirm = False
            elif side_mode == "short_only":
                long_confirm = False
            if not long_confirm and not short_confirm:
                continue
            side = "LONG" if long_confirm else "SHORT"
            entry = float(bc["c"])
            if side == "LONG":
                stop = min(float(b1["l"]), float(bc["l"])) - 0.01
            else:
                stop = max(float(b1["h"]), float(bc["h"])) + 0.01
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            out.append(
                Signal(
                    signal_key=f"ORB_BT:{symbol}:{bc['ts'].isoformat()}:{side}:{i}",
                    source="ORB",
                    source_event_id=f"{symbol}:{bc['ts'].isoformat()}:{i}",
                    symbol=symbol,
                    side=side,
                    signal_ts=ts_utc(pd.Timestamp(bc["ts"]).to_pydatetime()),
                    confidence=float(orb_conf),
                    weight=float(orb_weight),
                    strategy_id="ORB_CONFIRM_2CLOSE",
                    entry_price=entry,
                    stop_price=float(stop),
                    risk=float(risk),
                    payload={"or_high": or_high, "or_low": or_low},
                )
            )
    return out


def load_r6_signals(
    r6_db_path: str,
    start: str,
    end: str,
    *,
    r6_weight: float,
    stop_buffer: float,
    min_conf: float,
    require_long_post_or: bool,
) -> list[Signal]:
    conn = sqlite3.connect(r6_db_path)
    try:
        rows = conn.execute(
            """
            SELECT event_id, symbol, asof_date_cst, bar_close_ts_cst, decision, confidence, include_today_or,
                   candle_close, candle_low, candle_high, ref_high, ref_low, ref_width, primary_horizon
            FROM breakout_events
            WHERE decision IN ('LONG','SHORT')
              AND asof_date_cst >= ?
              AND asof_date_cst <= ?
            ORDER BY bar_close_ts_cst ASC
            """,
            (start, end),
        ).fetchall()
    finally:
        conn.close()
    out: list[Signal] = []
    for row in rows:
        (
            event_id,
            symbol,
            _,
            bar_ts,
            decision,
            conf,
            include_today_or,
            candle_close,
            candle_low,
            candle_high,
            ref_high,
            ref_low,
            ref_width,
            primary_horizon,
        ) = row
        side = str(decision).upper()
        confidence = float(conf or 0.0)
        if confidence < min_conf:
            continue
        if require_long_post_or and side == "LONG" and int(include_today_or or 0) == 0:
            continue
        signal_ts = ts_utc(pd.to_datetime(bar_ts).to_pydatetime())
        entry = float(candle_close or 0.0)
        if side == "LONG":
            stop = min(float(candle_low) - stop_buffer, entry - 0.01)
        else:
            stop = max(float(candle_high) + stop_buffer, entry + 0.01)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        out.append(
            Signal(
                signal_key=f"R6:{event_id}",
                source="R6",
                source_event_id=str(event_id),
                symbol=str(symbol).upper(),
                side=side,
                signal_ts=signal_ts,
                confidence=confidence,
                weight=float(r6_weight),
                strategy_id="R6",
                entry_price=entry,
                stop_price=float(stop),
                risk=float(risk),
                payload={
                    "ref_high": ref_high,
                    "ref_low": ref_low,
                    "ref_width": ref_width,
                    "primary_horizon": primary_horizon,
                },
            )
        )
    return out


def build_day_bar_map(bars: pd.DataFrame) -> dict[tuple[str, str], pd.DataFrame]:
    m: dict[tuple[str, str], pd.DataFrame] = {}
    if bars.empty:
        return m
    for (symbol, session_date), g in bars.groupby(["symbol", "session_date"], sort=False):
        m[(str(symbol), str(session_date))] = g.sort_values("ts").reset_index(drop=True)
    return m


def simulate_exit(
    day_bars: pd.DataFrame,
    *,
    entry_ts: datetime,
    side: str,
    stop_price: float,
    forced_exit_time: dtime,
) -> tuple[datetime, float, str]:
    after = day_bars[day_bars["ts"] > pd.Timestamp(entry_ts)].copy()
    if after.empty:
        return entry_ts, float(day_bars.iloc[-1]["c"]), "NO_FOLLOW_BAR"
    for _, r in after.iterrows():
        ts = ts_utc(pd.Timestamp(r["ts"]).to_pydatetime())
        bar_open = float(r["o"])
        bar_high = float(r["h"])
        bar_low = float(r["l"])
        bar_close = float(r["c"])
        t = pd.Timestamp(ts).tz_convert(CST).time()
        if side == "LONG" and bar_low <= stop_price:
            return ts, float(min(stop_price, bar_open)), "STOP"
        if side == "SHORT" and bar_high >= stop_price:
            return ts, float(max(stop_price, bar_open)), "STOP"
        if t >= forced_exit_time:
            return ts, bar_close, "TIME_EXIT"
    last = after.iloc[-1]
    return ts_utc(pd.Timestamp(last["ts"]).to_pydatetime()), float(last["c"]), "EOD_FALLBACK"


def compute_qty(equity: float, risk_pct: float, max_notional_pct: float, max_notional_dollars: float, entry: float, stop: float) -> int:
    risk = abs(entry - stop)
    if risk <= 0 or entry <= 0:
        return 0
    qty_risk = math.floor(max(0.0, equity * risk_pct) / risk)
    qty_notional_pct = math.floor(max(0.0, equity * max_notional_pct) / entry)
    qty_notional_abs = math.floor(max(0.0, max_notional_dollars) / entry)
    return max(0, int(min(qty_risk, qty_notional_pct, qty_notional_abs)))


def backtest_router(cfg: dict[str, Any], start: str, end: str) -> tuple[pd.DataFrame, dict]:
    orb_db = cfg["sources"]["orb"]["db_path"]
    r6_db = cfg["sources"]["r6"]["db_path"]

    # symbols from R6 universe by default
    conn = sqlite3.connect(r6_db)
    try:
        symbols = sorted({str(r[0]).upper() for r in conn.execute("SELECT DISTINCT symbol FROM breakout_events").fetchall()})
    finally:
        conn.close()
    if not symbols:
        raise RuntimeError("No symbols found in R6 breakout_events")

    bars = load_bars(orb_db, symbols, start, end)
    day_map = build_day_bar_map(bars)

    back_cfg = cfg.get("backtest", {}) or {}
    orb_signals: list[Signal] = []
    if bool(cfg["sources"]["orb"].get("enabled", True)):
        orb_signals = build_orb_signals(
            bars,
            side_mode=str(back_cfg.get("orb_side_mode", "long_only")).strip().lower(),
            orb_weight=float(cfg["sources"]["orb"].get("weight", 1.0)),
            orb_conf=float(cfg["sources"]["orb"].get("default_confidence", 0.72)),
        )
    r6_signals: list[Signal] = []
    if bool(cfg["sources"]["r6"].get("enabled", True)):
        r6_signals = load_r6_signals(
            r6_db,
            start,
            end,
            r6_weight=float(cfg["sources"]["r6"].get("weight", 1.0)),
            stop_buffer=float(cfg["execution"].get("r6_stop_buffer", 0.01)),
            min_conf=float(back_cfg.get("r6_min_confidence", 0.62)),
            require_long_post_or=bool(back_cfg.get("r6_require_long_post_or", True)),
        )
    signals = sorted([*orb_signals, *r6_signals], key=lambda s: (s.signal_ts, s.symbol))

    mode = str(cfg["execution"].get("arbitration_mode", "score_window")).strip().lower()
    decision_window = timedelta(seconds=int(cfg["execution"].get("decision_window_seconds", 180)))
    forced_hhmm = str(cfg["execution"].get("forced_exit_time_ct", "14:50"))
    forced_exit_time = dtime(int(forced_hhmm.split(":")[0]), int(forced_hhmm.split(":")[1]))
    risk_pct = float(cfg["execution"]["risk_pct_per_trade"])
    max_notional_pct = float(cfg["execution"]["max_notional_pct"])
    max_notional_dollars = float(cfg["execution"]["max_notional_dollars"])
    short_requires_inventory = bool(cfg["execution"].get("short_requires_inventory", True))
    initial_equity = float(back_cfg.get("initial_equity", 100000.0))

    per_symbol_pending: dict[str, list[Signal]] = defaultdict(list)
    per_symbol_window_end: dict[str, datetime] = {}
    per_symbol_open: dict[str, OpenPosition | None] = defaultdict(lambda: None)
    equity = initial_equity
    trades: list[dict] = []
    skipped: list[dict] = []

    def maybe_release_open(symbol: str, ts: datetime) -> None:
        nonlocal equity
        op = per_symbol_open.get(symbol)
        if op is None:
            return
        if ts >= op.exit_ts:
            pnl_per_share = (op.exit_price - op.entry_price) if op.side == "LONG" else (op.entry_price - op.exit_price)
            pnl = pnl_per_share * op.qty
            equity += pnl
            r_mult = (pnl_per_share / op.risk) if op.risk > 0 else 0.0
            trades.append(
                {
                    "symbol": op.symbol,
                    "source": op.source,
                    "source_event_id": op.source_event_id,
                    "strategy_id": op.strategy_id,
                    "side": op.side,
                    "confidence": op.confidence,
                    "score": op.score,
                    "qty": op.qty,
                    "entry_ts": op.entry_ts.isoformat(),
                    "exit_ts": op.exit_ts.isoformat(),
                    "entry_price": op.entry_price,
                    "stop_price": op.stop_price,
                    "exit_price": op.exit_price,
                    "exit_reason": op.exit_reason,
                    "risk": op.risk,
                    "r_mult": r_mult,
                    "pnl": pnl,
                    "equity_after": equity,
                }
            )
            per_symbol_open[symbol] = None

    def finalize_symbol(symbol: str, decision_ts: datetime) -> None:
        candidates = per_symbol_pending.get(symbol, [])
        if not candidates:
            return
        maybe_release_open(symbol, decision_ts)
        if per_symbol_open.get(symbol) is not None:
            for s in candidates:
                skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "position_open"})
            per_symbol_pending[symbol] = []
            return
        if mode == "first_signal_wins":
            chosen = sorted(candidates, key=lambda s: (s.signal_ts, -(s.weight * s.confidence)))[0]
        else:
            chosen = sorted(candidates, key=lambda s: (-(s.weight * s.confidence), s.signal_ts))[0]
        qty = compute_qty(equity, risk_pct, max_notional_pct, max_notional_dollars, chosen.entry_price, chosen.stop_price)
        if qty <= 0:
            for s in candidates:
                skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "qty_zero"})
            per_symbol_pending[symbol] = []
            return
        if chosen.side == "SHORT" and short_requires_inventory:
            skipped.append({"signal_key": chosen.signal_key, "symbol": symbol, "reason": "short_blocked_no_inventory"})
            for s in candidates:
                if s.signal_key != chosen.signal_key:
                    skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "lost_arbitration"})
            per_symbol_pending[symbol] = []
            return

        session_date = pd.Timestamp(chosen.signal_ts).tz_convert(CST).strftime("%Y-%m-%d")
        day_bars = day_map.get((symbol, session_date))
        if day_bars is None or day_bars.empty:
            for s in candidates:
                skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "no_bars"})
            per_symbol_pending[symbol] = []
            return

        exit_ts, exit_px, exit_reason = simulate_exit(
            day_bars,
            entry_ts=chosen.signal_ts,
            side=chosen.side,
            stop_price=chosen.stop_price,
            forced_exit_time=forced_exit_time,
        )
        per_symbol_open[symbol] = OpenPosition(
            symbol=symbol,
            side=chosen.side,
            qty=qty,
            entry_ts=chosen.signal_ts,
            entry_price=chosen.entry_price,
            stop_price=chosen.stop_price,
            risk=chosen.risk,
            exit_ts=exit_ts,
            exit_price=exit_px,
            exit_reason=exit_reason,
            source=chosen.source,
            source_event_id=chosen.source_event_id,
            strategy_id=chosen.strategy_id,
            confidence=chosen.confidence,
            score=chosen.weight * chosen.confidence,
        )
        for s in candidates:
            if s.signal_key == chosen.signal_key:
                continue
            skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "lost_arbitration"})
        per_symbol_pending[symbol] = []

    for s in signals:
        symbol = s.symbol
        maybe_release_open(symbol, s.signal_ts)
        if mode == "first_signal_wins":
            if per_symbol_open.get(symbol) is not None:
                skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "position_open"})
                continue
            per_symbol_pending[symbol] = [s]
            per_symbol_window_end[symbol] = s.signal_ts
            finalize_symbol(symbol, s.signal_ts)
            continue
        if per_symbol_pending[symbol] and s.signal_ts > per_symbol_window_end[symbol]:
            finalize_symbol(symbol, per_symbol_window_end[symbol])
            maybe_release_open(symbol, s.signal_ts)
        if per_symbol_open.get(symbol) is not None:
            skipped.append({"signal_key": s.signal_key, "symbol": symbol, "reason": "position_open"})
            continue
        if not per_symbol_pending[symbol]:
            per_symbol_pending[symbol] = [s]
            per_symbol_window_end[symbol] = s.signal_ts + decision_window
        else:
            per_symbol_pending[symbol].append(s)

    # finalize leftover windows + positions
    for symbol, cands in list(per_symbol_pending.items()):
        if cands:
            finalize_symbol(symbol, per_symbol_window_end[symbol])
    far_future = datetime(2100, 1, 1, tzinfo=UTC)
    for symbol in list(per_symbol_open.keys()):
        maybe_release_open(symbol, far_future)

    trades_df = pd.DataFrame(trades)
    skipped_df = pd.DataFrame(skipped)
    if trades_df.empty:
        summary = {
            "status": "completed",
            "start_date": start,
            "end_date": end,
            "signals_total": int(len(signals)),
            "trades_total": 0,
            "skipped_total": int(len(skipped_df)),
            "initial_equity": initial_equity,
            "final_equity": initial_equity,
            "total_return_pct": 0.0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
        }
        return trades_df, {"summary": summary, "skipped": skipped_df}

    trades_df = trades_df.sort_values("exit_ts").reset_index(drop=True)
    eq = trades_df["equity_after"].astype(float)
    dd = eq / eq.cummax() - 1.0
    r = trades_df["r_mult"].astype(float)
    gp = float(r[r > 0].sum())
    gl = abs(float(r[r < 0].sum()))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    source_perf = []
    for source, g in trades_df.groupby("source", sort=True):
        rr = g["r_mult"].astype(float)
        pnl_sum = float(g["pnl"].astype(float).sum())
        source_perf.append(
            {
                "source": str(source),
                "trades": int(len(g)),
                "win_rate": float((rr > 0).mean()),
                "avg_r": float(rr.mean()),
                "pnl": pnl_sum,
                "return_pct": float((pnl_sum / initial_equity) * 100.0),
            }
        )
    skip_reasons = skipped_df["reason"].value_counts().to_dict() if not skipped_df.empty else {}
    summary = {
        "status": "completed",
        "start_date": start,
        "end_date": end,
        "signals_total": int(len(signals)),
        "trades_total": int(len(trades_df)),
        "skipped_total": int(len(skipped_df)),
        "initial_equity": float(initial_equity),
        "final_equity": float(eq.iloc[-1]),
        "total_return_pct": float((eq.iloc[-1] / initial_equity - 1.0) * 100.0),
        "win_rate": float((r > 0).mean()),
        "avg_r": float(r.mean()),
        "profit_factor": float(pf),
        "max_drawdown_pct": float(abs(dd.min()) * 100.0),
        "skip_reasons": skip_reasons,
        "source_performance": source_perf,
    }
    return trades_df, {"summary": summary, "skipped": skipped_df}


def main() -> None:
    args = parse_args()
    cfg = load_cfg(args.config)
    trades_df, payload = backtest_router(cfg, args.start, args.end)
    summary = payload["summary"]
    skipped_df = payload["skipped"]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    trades_path = out_dir / f"router_backtest_trades_{stamp}.csv"
    skipped_path = out_dir / f"router_backtest_skipped_{stamp}.csv"
    summary_path = out_dir / f"router_backtest_summary_{stamp}.json"
    trades_df.to_csv(trades_path, index=False)
    skipped_df.to_csv(skipped_path, index=False)
    summary["artifacts"] = {
        "trades": str(trades_path).replace("\\", "/"),
        "skipped": str(skipped_path).replace("\\", "/"),
        "summary": str(summary_path).replace("\\", "/"),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
