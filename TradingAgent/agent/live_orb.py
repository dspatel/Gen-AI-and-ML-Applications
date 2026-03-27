from __future__ import annotations

import json
import math
import time as time_module
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path

import pandas as pd
import requests

from agent.broker import AlpacaTradingClient
from agent.config import CHICAGO_TZ, FORCED_EXIT_TIME, OR_END, SESSION_START
from agent.data import load_intraday_data
from agent.db import Database
from agent.selection import SelectionConfig, StrategyReselector, should_reselect
from agent.strategy.orb import NO_PROGRESS_BARS_BY_TF, NO_PROGRESS_TARGET_R, BREAKEVEN_TRIGGER_R
from agent.strategy_spec import StrategySpec, parse_strategy_id


STACK_COMPONENT_CODES = {
    "TSNP": "TIME_STOP_NO_PROGRESS",
    "ORRF": "OR_REENTRY_FAIL",
    "BER": "BREAKEVEN_RATCHET",
}

LIVE_SUPPORTED_EXIT_VARIANTS = {
    "FIXED_2R",
    "EMA20_TRAIL",
    "TIME_STOP_NO_PROGRESS",
    "OR_REENTRY_FAIL",
    "BREAKEVEN_RATCHET",
}


@dataclass(frozen=True)
class LiveTradeConfig:
    symbols: list[str]
    asof_date: str | None
    frequency: str
    side_mode: str
    lookback_months: int
    validation_months: int
    min_train_trades: int
    min_val_trades: int
    data_provider: str
    selection_data_provider: str
    alpaca_env_prefix: str | None = None
    alpaca_feed: str = "iex"
    db_path: str = "orb_research.db"
    dry_run: bool = True
    risk_pct_per_trade: float = 0.005
    max_notional_pct: float = 0.20
    max_notional_dollars: float = 5000.0
    max_open_positions: int = 8
    default_equity: float = 100000.0
    force_reselect: bool = False
    default_strategy_id: str = "TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE"
    discord_enabled: bool = False
    discord_webhook_url: str = ""
    short_requires_inventory: bool = True
    gap_entry_enabled: bool = False
    gap_entry_timeframe_min: int = 15
    gap_entry_apply_on_limit1: bool = False
    gap_entry_gap_threshold: float = 0.0015
    gap_entry_ema_dist_min: float = 0.001
    gap_entry_ema_dist_max: float = 0.012
    gap_entry_require_close_compare: bool = True
    gap_entry_require_body_direction: bool = True
    live_entry_max_age_bars: int = 1


class LiveOrbTrader:
    def __init__(self, config: LiveTradeConfig):
        self.config = config
        self.db = Database(Path(config.db_path))
        self.broker = None if config.dry_run else AlpacaTradingClient.from_env(env_prefix="ORB")
        self._prev_close_cache: dict[tuple[str, str], float | None] = {}
        if not config.dry_run and self.broker is None:
            raise RuntimeError("Missing Alpaca credentials for live trading mode")

    def run(self) -> dict:
        asof = self._resolve_asof_date()
        selector = StrategyReselector(
            SelectionConfig(
                symbols=self.config.symbols,
                asof_date=asof.isoformat(),
                frequency=self.config.frequency,
                side_mode=self.config.side_mode,
                lookback_months=self.config.lookback_months,
                validation_months=self.config.validation_months,
                min_train_trades=self.config.min_train_trades,
                min_val_trades=self.config.min_val_trades,
                data_provider=self.config.selection_data_provider,
                db_path=self.config.db_path,
                alpaca_env_prefix=self.config.alpaca_env_prefix,
                alpaca_feed=self.config.alpaca_feed,
            )
        )

        last_asof = selector.latest_active_asof_date()
        if self.config.force_reselect or should_reselect(last_asof, asof, self.config.frequency):
            reselection = selector.run()
        else:
            reselection = {"status": "skipped", "reason": "selection_map_current"}

        strategy_map = selector.fetch_active_map(asof_date=asof.isoformat())
        if not strategy_map:
            raise RuntimeError("No active strategy map found; run --mode reselect first")

        account_equity = self._account_equity()
        events: list[dict] = []

        for symbol in self.config.symbols:
            strategy_id = strategy_map.get(symbol, self.config.default_strategy_id)
            spec = parse_strategy_id(strategy_id)
            if not self._is_live_supported(spec):
                self._log_event(
                    level="WARN",
                    symbol=symbol,
                    event_type="strategy_fallback",
                    message=f"Unsupported live exit '{spec.exit_variant}', using fallback strategy",
                    data={"from": strategy_id, "to": self.config.default_strategy_id},
                )
                spec = parse_strategy_id(self.config.default_strategy_id)

            open_pos = self._get_open_position(symbol)
            data_tf = int(open_pos.get("timeframe_min")) if open_pos is not None and open_pos.get("timeframe_min") else int(spec.timeframe_min)
            bars_tf, provider = load_intraday_data(
                symbol=symbol,
                start=asof.isoformat(),
                end=asof.isoformat(),
                provider=self.config.data_provider,
                timeframe_min=data_tf,
                env_prefix=self.config.alpaca_env_prefix,
                alpaca_feed=self.config.alpaca_feed,
            )
            bars_tf = self._filter_to_completed_bars(bars_tf, timeframe_min=data_tf)
            if bars_tf.empty:
                events.append({"symbol": symbol, "status": "no_data", "timeframe_min": data_tf})
                continue

            if open_pos is not None:
                if not self.config.dry_run:
                    reconciled = self._reconcile_position_with_broker(symbol=symbol, open_pos=open_pos, bars_tf=bars_tf)
                    if reconciled is not None:
                        events.append({"symbol": symbol, **reconciled})
                        continue
                manage_spec = parse_strategy_id(str(open_pos.get("strategy_id") or spec.strategy_id))
                result = self._manage_open_position(open_pos, manage_spec, bars_tf)
                events.append({"symbol": symbol, **result})
                continue

            if self._open_position_count() >= self.config.max_open_positions:
                events.append({"symbol": symbol, "status": "max_open_positions_reached"})
                continue

            result = self._try_open_new_position(
                symbol=symbol,
                spec=spec,
                bars_tf=bars_tf,
                account_equity=account_equity,
                provider=provider,
            )
            events.append({"symbol": symbol, **result})

        summary = {
            "mode": "trade",
            "asof_date": asof.isoformat(),
            "dry_run": self.config.dry_run,
            "frequency": self.config.frequency,
            "side_mode": self.config.side_mode,
            "reselection": reselection,
            "events": events,
        }
        Path("live_trade_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        return summary

    def _try_open_new_position(
        self,
        symbol: str,
        spec: StrategySpec,
        bars_tf: pd.DataFrame,
        account_equity: float,
        provider: str,
    ) -> dict:
        tf = _prepare_frame(bars_tf)
        if tf.empty:
            return {"status": "no_tf_data", "provider": provider, "strategy_id": spec.strategy_id}

        session_date = str(tf.iloc[-1]["session_date"])
        if spec.trade_limit_1d == 1 and self._entries_today(symbol, session_date) >= 1:
            return {"status": "trade_limit_reached", "strategy_id": spec.strategy_id}

        prev_session_close = None
        if self.config.gap_entry_enabled:
            prev_session_close = self._previous_session_close(symbol=symbol, session_date=pd.Timestamp(session_date).date())
        signal = _find_latest_entry_signal(
            tf,
            spec,
            side_mode=self.config.side_mode,
            gap_entry_enabled=self.config.gap_entry_enabled,
            gap_timeframe_min=self.config.gap_entry_timeframe_min,
            gap_apply_on_limit1=self.config.gap_entry_apply_on_limit1,
            gap_threshold=self.config.gap_entry_gap_threshold,
            gap_dist_min=self.config.gap_entry_ema_dist_min,
            gap_dist_max=self.config.gap_entry_ema_dist_max,
            gap_require_close_compare=self.config.gap_entry_require_close_compare,
            gap_require_body_direction=self.config.gap_entry_require_body_direction,
            prev_session_close=prev_session_close,
        )
        if signal is None:
            return {"status": "no_entry_signal", "strategy_id": spec.strategy_id}

        # Restart safety: avoid entering stale signals from earlier bars in the same session.
        try:
            latest_bar_ts = pd.to_datetime(tf.iloc[-1]["ts"], utc=True)
            signal_ts = pd.to_datetime(str(signal.get("entry_ts") or ""), utc=True)
            max_age_bars = max(1, int(getattr(self.config, "live_entry_max_age_bars", 1)))
            max_age_min = max(1, int(spec.timeframe_min) * max_age_bars)
            age_min = (latest_bar_ts - signal_ts).total_seconds() / 60.0
            if age_min > float(max_age_min):
                self._record_missed_trade(
                    symbol=symbol,
                    strategy_id=spec.strategy_id,
                    signal=signal,
                    planned_qty=0,
                    reason="STALE_SIGNAL_SKIPPED",
                    extra={
                        "latest_bar_ts": latest_bar_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                        "signal_ts": signal_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                        "signal_age_min": round(float(age_min), 3),
                        "max_age_min": int(max_age_min),
                    },
                )
                self._log_event(
                    level="WARN",
                    symbol=symbol,
                    event_type="stale_signal_skipped",
                    message=f"Skipped stale signal for {symbol}",
                    data={
                        "strategy_id": spec.strategy_id,
                        "signal_ts": signal_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                        "latest_bar_ts": latest_bar_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                        "signal_age_min": round(float(age_min), 3),
                        "max_age_min": int(max_age_min),
                    },
                )
                return {
                    "status": "stale_signal_skipped",
                    "strategy_id": spec.strategy_id,
                    "signal_age_min": round(float(age_min), 3),
                    "max_age_min": int(max_age_min),
                }
        except Exception:
            pass
        if not self._claim_signal_lock(symbol=symbol, strategy_id=spec.strategy_id, signal=signal):
            return {
                "status": "duplicate_signal_ignored",
                "strategy_id": spec.strategy_id,
                "side": signal["side"],
                "entry_ts": signal["entry_ts"],
            }

        self._log_event(
            level="INFO",
            symbol=symbol,
            event_type="entry_signal_detected",
            message=f"Detected {signal['side']} breakout confirmation for {symbol}",
            data={
                "strategy_id": spec.strategy_id,
                "exit_variant": spec.exit_variant,
                "trade_limit_1d": int(spec.trade_limit_1d),
                "long_cutoff_ct": spec.long_cutoff.strftime("%H:%M") if spec.long_cutoff else "NONE",
                "side": signal["side"],
                "entry_ts": signal["entry_ts"],
                "entry_price": signal["entry_price"],
                "stop_price": signal["stop_price"],
                "risk": signal["risk"],
                "or_high": signal["or_high"],
                "or_low": signal["or_low"],
                "or_width": signal["or_width"],
                "timeframe_min": spec.timeframe_min,
                "data_provider": provider,
                "entry_bar_open": signal["entry_bar_open"],
                "entry_bar_high": signal["entry_bar_high"],
                "entry_bar_low": signal["entry_bar_low"],
                "entry_bar_close": signal["entry_bar_close"],
                "prev_bar_close": signal["prev_bar_close"],
                "prev2_bar_close": signal["prev2_bar_close"],
                "signal_source": signal.get("signal_source", "ORB_CONFIRM"),
                "gap_pct": signal.get("gap_pct"),
                "dist0": signal.get("dist0"),
                "dist1": signal.get("dist1"),
            },
        )

        qty = _compute_position_size(
            equity=account_equity,
            risk_pct=self.config.risk_pct_per_trade,
            max_notional_pct=self.config.max_notional_pct,
            max_notional_dollars=self.config.max_notional_dollars,
            entry_price=signal["entry_price"],
            stop_price=signal["stop_price"],
        )
        if qty <= 0:
            return {"status": "qty_zero", "strategy_id": spec.strategy_id}

        if (
            signal["side"] == "SHORT"
            and self.config.short_requires_inventory
            and not self.config.dry_run
        ):
            avail = self._available_long_qty(symbol)
            if avail < int(qty):
                self._record_missed_trade(
                    symbol=symbol,
                    strategy_id=spec.strategy_id,
                    signal=signal,
                    planned_qty=int(qty),
                    reason="SHORT_BLOCKED_NO_INVENTORY",
                    extra={"available_long_qty": int(avail)},
                )
                self._log_event(
                    level="WARN",
                    symbol=symbol,
                    event_type="short_blocked_no_inventory",
                    message=f"Blocked SHORT for {symbol}: required qty={qty}, available long qty={avail}",
                    data={
                        "strategy_id": spec.strategy_id,
                        "planned_qty": int(qty),
                        "available_long_qty": int(avail),
                        "entry_price": signal["entry_price"],
                    },
                )
                return {
                    "status": "short_blocked_no_inventory",
                    "strategy_id": spec.strategy_id,
                    "planned_qty": int(qty),
                    "available_long_qty": int(avail),
                }

        client_oid = f"orb-{symbol}-{uuid.uuid4().hex[:20]}"
        broker_order_id = None
        stop_order_id = None
        stop_status = "attached"
        if not self.config.dry_run:
            pre_pos = self.broker.get_open_position(symbol)
            pre_signed_qty = self._signed_qty_from_position(pre_pos)
            pre_side = str((pre_pos or {}).get("side") or "")
            try:
                side = "buy" if signal["side"] == "LONG" else "sell"
                order = self.broker.submit_market_order(symbol=symbol, side=side, qty=qty, client_order_id=client_oid)
                broker_order_id = str(order.get("id"))
                self._wait_for_order_to_leave_open_book(symbol=symbol, order_id=broker_order_id, timeout_seconds=8.0, poll_seconds=0.5)

                stop_side = "sell" if signal["side"] == "LONG" else "buy"
                stop_attach_attempts = 5
                last_stop_exc: Exception | None = None
                for attempt in range(1, stop_attach_attempts + 1):
                    stop_oid = f"orb-stop-{symbol}-{uuid.uuid4().hex[:16]}"
                    try:
                        stop_order = self.broker.submit_stop_order(
                            symbol=symbol,
                            side=stop_side,
                            qty=qty,
                            stop_price=signal["stop_price"],
                            client_order_id=stop_oid,
                        )
                        stop_order_id = self._normalize_order_id(stop_order.get("id"))
                        if stop_order_id:
                            break
                    except Exception as exc:
                        last_stop_exc = exc
                        self._log_event(
                            level="WARN",
                            symbol=symbol,
                            event_type="stop_attach_retry",
                            message=f"Stop attach retry {attempt}/{stop_attach_attempts} for {symbol}",
                            data={
                                "strategy_id": spec.strategy_id,
                                "attempt": int(attempt),
                                "max_attempts": int(stop_attach_attempts),
                                "error": self._error_text(exc),
                            },
                        )
                        time_module.sleep(1.0)

                if not stop_order_id:
                    stop_status = "failed_flattened"
                    restore_result = self._restore_symbol_exposure(symbol=symbol, target_signed_qty=pre_signed_qty)
                    self._record_missed_trade(
                        symbol=symbol,
                        strategy_id=spec.strategy_id,
                        signal=signal,
                        planned_qty=int(qty),
                        reason="STOP_ATTACH_FAILED_FLATTENED",
                        extra={
                            "pre_signed_qty": int(pre_signed_qty),
                            "pre_side": pre_side,
                            "restore_result": restore_result,
                            "error": self._error_text(last_stop_exc) if last_stop_exc else "unknown",
                        },
                    )
                    self._log_event(
                        level="ERROR",
                        symbol=symbol,
                        event_type="stop_attach_failed_flattened",
                        message=f"Failed to attach protective stop for {symbol}; exposure restoration attempted",
                        data={
                            "strategy_id": spec.strategy_id,
                            "side": signal["side"],
                            "qty": int(qty),
                            "stop_price": float(signal["stop_price"]),
                            "attempts": int(stop_attach_attempts),
                            "restore_result": restore_result,
                            "error": self._error_text(last_stop_exc) if last_stop_exc else "unknown",
                        },
                    )
                    return {
                        "status": "stop_attach_failed_flattened",
                        "strategy_id": spec.strategy_id,
                        "qty": int(qty),
                        "side": signal["side"],
                        "restore_result": restore_result,
                    }
            except Exception as exc:
                if signal["side"] == "SHORT" and self._is_short_unavailable_error(exc):
                    reason = "SHORT_NOT_AVAILABLE"
                    event_type = "short_not_available"
                    level = "WARN"
                else:
                    reason = "ENTRY_SUBMIT_FAILED"
                    event_type = "entry_submit_failed"
                    level = "ERROR"
                self._record_missed_trade(
                    symbol=symbol,
                    strategy_id=spec.strategy_id,
                    signal=signal,
                    planned_qty=int(qty),
                    reason=reason,
                    extra={"error": self._error_text(exc)},
                )
                self._log_event(
                    level=level,
                    symbol=symbol,
                    event_type=event_type,
                    message=f"Entry submit failed for {symbol}",
                    data={
                        "strategy_id": spec.strategy_id,
                        "side": signal["side"],
                        "qty": int(qty),
                        "entry_price": float(signal["entry_price"]),
                        "error": self._error_text(exc),
                    },
                )
                return {"status": reason.lower(), "strategy_id": spec.strategy_id, "side": signal["side"]}

        position_id = str(uuid.uuid4())
        self.db.execute(
            """
            INSERT INTO live_positions
            (
                position_id, symbol, strategy_id, session_date, side, qty, entry_ts, entry_price,
                initial_stop_price, stop_price, risk, or_high, or_low, timeframe_min, exit_variant, trade_limit_1d,
                long_cutoff_ct, progress_hit, be_armed, bars_since_entry, status, broker_order_id, stop_order_id, data_provider, last_bar_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 'OPEN', ?, ?, ?, ?)
            """,
            (
                position_id,
                symbol,
                spec.strategy_id,
                session_date,
                signal["side"],
                int(qty),
                signal["entry_ts"],
                float(signal["entry_price"]),
                float(signal["stop_price"]),
                float(signal["stop_price"]),
                float(signal["risk"]),
                float(signal["or_high"]),
                float(signal["or_low"]),
                int(spec.timeframe_min),
                spec.exit_variant,
                int(spec.trade_limit_1d),
                spec.long_cutoff.strftime("%H:%M") if spec.long_cutoff else "NONE",
                broker_order_id,
                stop_order_id,
                provider,
                signal["entry_ts"],
            ),
        )
        self._log_event(
            level="INFO",
            symbol=symbol,
            event_type="entry_opened",
            message=f"Opened {signal['side']} position for {symbol}",
            data={
                "strategy_id": spec.strategy_id,
                "exit_variant": spec.exit_variant,
                "trade_limit_1d": int(spec.trade_limit_1d),
                "long_cutoff_ct": spec.long_cutoff.strftime("%H:%M") if spec.long_cutoff else "NONE",
                "side": signal["side"],
                "qty": qty,
                "entry_price": signal["entry_price"],
                "stop_price": signal["stop_price"],
                "risk": signal["risk"],
                "or_high": signal["or_high"],
                "or_low": signal["or_low"],
                "or_width": signal["or_width"],
                "timeframe_min": spec.timeframe_min,
                "data_provider": provider,
                "entry_ts": signal["entry_ts"],
                "broker_order_id": broker_order_id,
                "stop_order_id": stop_order_id,
                "position_id": position_id,
                "stop_status": stop_status,
                "stop_order_attached": bool(stop_order_id),
                "entry_bar_open": signal["entry_bar_open"],
                "entry_bar_high": signal["entry_bar_high"],
                "entry_bar_low": signal["entry_bar_low"],
                "entry_bar_close": signal["entry_bar_close"],
                "prev_bar_close": signal["prev_bar_close"],
                "prev2_bar_close": signal["prev2_bar_close"],
                "signal_source": signal.get("signal_source", "ORB_CONFIRM"),
                "gap_pct": signal.get("gap_pct"),
                "dist0": signal.get("dist0"),
                "dist1": signal.get("dist1"),
            },
        )
        return {
            "status": "entry_opened",
            "strategy_id": spec.strategy_id,
            "qty": qty,
            "side": signal["side"],
            "stop_status": stop_status,
        }

    def _manage_open_position(self, open_pos: dict, spec: StrategySpec, bars_tf: pd.DataFrame) -> dict:
        tf = _prepare_frame(bars_tf)
        if tf.empty:
            return {"status": "no_tf_data_for_management", "strategy_id": spec.strategy_id}

        side = str(open_pos["side"])
        if not self.config.dry_run:
            self._ensure_stop_order(open_pos=open_pos, side=side, stop_price=float(open_pos["stop_price"]))

        entry_ts = pd.to_datetime(open_pos["entry_ts"], utc=True)
        after_entry = tf[pd.to_datetime(tf["ts"], utc=True) > entry_ts]
        if after_entry.empty:
            return {"status": "no_new_bars", "strategy_id": spec.strategy_id}

        if open_pos.get("last_bar_ts"):
            last_bar_ts = pd.to_datetime(open_pos["last_bar_ts"], utc=True)
            after_entry = after_entry[pd.to_datetime(after_entry["ts"], utc=True) > last_bar_ts]
        if after_entry.empty:
            return {"status": "no_incremental_bars", "strategy_id": spec.strategy_id}

        state = {
            "trail": float(open_pos["stop_price"]),
            "progress_hit": bool(open_pos["progress_hit"]),
            "be_armed": bool(open_pos["be_armed"]),
            "bars_since_entry": int(open_pos["bars_since_entry"]),
        }
        entry_price = float(open_pos["entry_price"])
        risk = float(open_pos["risk"])
        or_high = float(open_pos["or_high"])
        or_low = float(open_pos["or_low"])

        exit_event = None
        for _, bar in after_entry.sort_values("ts").iterrows():
            state["bars_since_entry"] += 1
            high = float(bar["h"])
            low = float(bar["l"])
            close = float(bar["c"])
            ema = float(bar["ema20"])
            bar_ts = pd.Timestamp(bar["ts"])
            bar_time = bar_ts.tz_convert(CHICAGO_TZ).time()

            progress_price = entry_price + NO_PROGRESS_TARGET_R * risk if side == "LONG" else entry_price - NO_PROGRESS_TARGET_R * risk
            be_trigger = entry_price + BREAKEVEN_TRIGGER_R * risk if side == "LONG" else entry_price - BREAKEVEN_TRIGGER_R * risk
            if side == "LONG":
                if high >= progress_price:
                    state["progress_hit"] = True
                if high >= be_trigger:
                    state["be_armed"] = True
            else:
                if low <= progress_price:
                    state["progress_hit"] = True
                if low <= be_trigger:
                    state["be_armed"] = True

            exit_event = _evaluate_exit_incremental(
                side=side,
                exit_variant=spec.exit_variant,
                bar_time=bar_time,
                high=high,
                low=low,
                close=close,
                ema=ema,
                trail=state["trail"],
                entry_price=entry_price,
                risk=risk,
                or_high=or_high,
                or_low=or_low,
                progress_hit=state["progress_hit"],
                be_armed=state["be_armed"],
                bars_since_entry=state["bars_since_entry"],
                timeframe_min=spec.timeframe_min,
            )
            state["trail"] = float(exit_event["trail"])

            if exit_event["should_exit"]:
                closed_ok = self._close_position(
                    open_pos=open_pos,
                    side=side,
                    exit_price=exit_event["exit_price"],
                    reason=exit_event["reason"],
                    ts=bar_ts,
                )
                if closed_ok:
                    return {"status": "position_closed", "strategy_id": spec.strategy_id, "reason": exit_event["reason"]}
                return {"status": "exit_pending_partial", "strategy_id": spec.strategy_id, "reason": exit_event["reason"]}

            if exit_event["new_stop"]:
                self._update_stop_order(open_pos=open_pos, side=side, new_stop=state["trail"])
                state["trail"] = float(open_pos["stop_price"])

            self.db.execute(
                """
                UPDATE live_positions
                SET stop_price = ?, progress_hit = ?, be_armed = ?, bars_since_entry = ?, last_bar_ts = ?
                WHERE position_id = ? AND status = 'OPEN'
                """,
                (
                    float(state["trail"]),
                    int(state["progress_hit"]),
                    int(state["be_armed"]),
                    int(state["bars_since_entry"]),
                    bar_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                    open_pos["position_id"],
                ),
            )

        return {"status": "position_managed", "strategy_id": spec.strategy_id, "last_stop": state["trail"]}

    def _reconcile_position_with_broker(self, symbol: str, open_pos: dict, bars_tf: pd.DataFrame) -> dict | None:
        broker_pos = self.broker.get_open_position(symbol)
        if broker_pos is not None:
            return None
        close_px = float(bars_tf.iloc[-1]["c"]) if not bars_tf.empty else float(open_pos["entry_price"])
        ts = pd.to_datetime(bars_tf.iloc[-1]["ts"], utc=True) if not bars_tf.empty else pd.Timestamp.now(tz="UTC")
        closed_ok = self._close_position(
            open_pos=open_pos,
            side=str(open_pos["side"]),
            exit_price=close_px,
            reason="BROKER_SYNC_FLAT",
            ts=ts,
            send_broker_exit=False,
        )
        if closed_ok:
            return {"status": "position_closed", "strategy_id": str(open_pos["strategy_id"]), "reason": "BROKER_SYNC_FLAT"}
        return {"status": "broker_sync_pending", "strategy_id": str(open_pos["strategy_id"]), "reason": "BROKER_SYNC_FLAT"}

    def _close_position(
        self,
        open_pos: dict,
        side: str,
        exit_price: float,
        reason: str,
        ts: pd.Timestamp,
        send_broker_exit: bool = True,
    ) -> bool:
        symbol = str(open_pos["symbol"])
        qty = int(open_pos["qty"])
        attempted_broker_exit = bool((not self.config.dry_run) and send_broker_exit)
        if (not self.config.dry_run) and send_broker_exit:
            try:
                broker_pos = self.broker.get_open_position(symbol)
                broker_signed = self._signed_qty_from_position(broker_pos)
                expected_sign = 1 if side == "LONG" else -1
                if broker_signed == 0:
                    send_broker_exit = False
                elif (1 if broker_signed > 0 else -1) != expected_sign:
                    try:
                        self.broker.close_position(symbol)
                    except Exception:
                        pass
                    send_broker_exit = False
                else:
                    qty = min(int(qty), abs(int(broker_signed)))
                    if qty <= 0:
                        send_broker_exit = False

                stop_order_id = self._normalize_order_id(open_pos.get("stop_order_id"))
                if stop_order_id:
                    try:
                        self.broker.cancel_order(stop_order_id)
                    except Exception:
                        pass
                if send_broker_exit:
                    exit_side = "sell" if side == "LONG" else "buy"
                    exit_oid = f"orb-exit-{symbol}-{uuid.uuid4().hex[:16]}"
                    exit_order = self.broker.submit_market_order(
                        symbol=symbol,
                        side=exit_side,
                        qty=int(qty),
                        client_order_id=exit_oid,
                    )
                    self._wait_for_order_to_leave_open_book(
                        symbol=symbol,
                        order_id=self._normalize_order_id((exit_order or {}).get("id")),
                        timeout_seconds=6.0,
                        poll_seconds=0.4,
                    )
            except Exception:
                # Fallback for edge cases where qty-based close fails due broker-side netting/partials.
                try:
                    self.broker.close_position(symbol)
                except Exception:
                    pass

        if attempted_broker_exit:
            flat_ok = self._attempt_force_flatten_symbol(symbol=symbol, attempts=3, sleep_seconds=0.75)
            if not flat_ok:
                broker_pos = self.broker.get_open_position(symbol) if self.broker is not None else None
                residual_qty = abs(int(self._signed_qty_from_position(broker_pos)))
                if residual_qty > 0:
                    self.db.execute(
                        """
                        UPDATE live_positions
                        SET qty = ?, stop_order_id = NULL, last_bar_ts = ?
                        WHERE position_id = ? AND status = 'OPEN'
                        """,
                        (int(residual_qty), ts.strftime("%Y-%m-%d %H:%M:%S%z"), open_pos["position_id"]),
                    )
                    open_pos["qty"] = int(residual_qty)
                    open_pos["stop_order_id"] = None
                    try:
                        self._ensure_stop_order(open_pos=open_pos, side=side, stop_price=float(open_pos["stop_price"]))
                    except Exception:
                        pass
                self._log_event(
                    level="WARN",
                    symbol=symbol,
                    event_type="exit_partial_unfilled",
                    message=f"Exit for {symbol} not fully filled; position remains OPEN",
                    data={
                        "reason": reason,
                        "position_id": str(open_pos["position_id"]),
                        "broker_side": str((broker_pos or {}).get("side") or ""),
                        "broker_qty": str((broker_pos or {}).get("qty") or ""),
                    },
                )
                return False

        self.db.execute(
            """
            UPDATE live_positions
            SET status = 'CLOSED', exit_ts = ?, exit_price = ?, exit_reason = ?, stop_order_id = NULL
            WHERE position_id = ? AND status = 'OPEN'
            """,
            (ts.strftime("%Y-%m-%d %H:%M:%S%z"), float(exit_price), reason, open_pos["position_id"]),
        )
        entry_price = float(open_pos["entry_price"])
        pnl_per_share = (float(exit_price) - entry_price) if side == "LONG" else (entry_price - float(exit_price))
        pnl = pnl_per_share * qty
        risk = float(open_pos["risk"])
        r_mult = (pnl_per_share / risk) if risk > 0 else 0.0
        pnl_pct = (pnl_per_share / entry_price) if entry_price > 0 else 0.0
        initial_stop = float(open_pos.get("initial_stop_price") or open_pos["stop_price"])
        final_stop = float(open_pos.get("stop_price") or initial_stop)
        self.db.execute(
            """
            INSERT INTO live_trades
            (
                trade_id, position_id, symbol, strategy_id, session_date, side, qty,
                entry_ts, exit_ts, entry_price, exit_price, initial_stop_price, final_stop_price,
                risk, pnl, pnl_pct, r_mult, exit_reason, timeframe_min, exit_variant,
                trade_limit_1d, long_cutoff_ct, data_provider
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                entry_price,
                float(exit_price),
                initial_stop,
                final_stop,
                risk,
                pnl,
                pnl_pct,
                r_mult,
                reason,
                int(open_pos["timeframe_min"]),
                str(open_pos["exit_variant"]),
                int(open_pos["trade_limit_1d"]),
                str(open_pos["long_cutoff_ct"]),
                str(open_pos.get("data_provider") or ""),
            ),
        )
        self._log_event(
            level="INFO",
            symbol=symbol,
            event_type="position_closed",
            message=f"Closed {symbol} due to {reason}",
            data={
                "exit_price": exit_price,
                "reason": reason,
                "position_id": open_pos["position_id"],
                "pnl": pnl,
                "r_mult": r_mult,
                "side": side,
                "strategy_id": str(open_pos["strategy_id"]),
                "exit_variant": str(open_pos.get("exit_variant") or ""),
                "timeframe_min": int(open_pos.get("timeframe_min") or 0),
                "trade_limit_1d": int(open_pos.get("trade_limit_1d") or 0),
                "long_cutoff_ct": str(open_pos.get("long_cutoff_ct") or ""),
                "qty": qty,
                "entry_price": entry_price,
                "entry_ts": str(open_pos["entry_ts"]),
                "stop_price": float(open_pos.get("stop_price") or 0.0),
            },
        )
        return True

    def _update_stop_order(self, open_pos: dict, side: str, new_stop: float) -> None:
        old_stop = float(open_pos["stop_price"])
        if abs(new_stop - old_stop) < 0.01:
            return
        if self.config.dry_run:
            return
        old_stop_order_id = self._normalize_order_id(open_pos.get("stop_order_id"))
        old_stop_canceled = False
        if old_stop_order_id:
            try:
                self.broker.cancel_order(old_stop_order_id)
                old_stop_canceled = True
            except Exception:
                pass
        stop_side = "sell" if side == "LONG" else "buy"
        client_oid = f"orb-stop-update-{open_pos['symbol']}-{uuid.uuid4().hex[:16]}"
        try:
            order = self.broker.submit_stop_order(
                symbol=str(open_pos["symbol"]),
                side=stop_side,
                qty=int(open_pos["qty"]),
                stop_price=float(new_stop),
                client_order_id=client_oid,
            )
            stop_order_id = self._normalize_order_id(order.get("id"))
        except Exception as exc:
            restored_stop_id = None
            if old_stop_canceled:
                try:
                    restore_oid = f"orb-stop-restore-{open_pos['symbol']}-{uuid.uuid4().hex[:16]}"
                    restored = self.broker.submit_stop_order(
                        symbol=str(open_pos["symbol"]),
                        side=stop_side,
                        qty=int(open_pos["qty"]),
                        stop_price=float(old_stop),
                        client_order_id=restore_oid,
                    )
                    restored_stop_id = self._normalize_order_id(restored.get("id"))
                except Exception:
                    restored_stop_id = None
            if restored_stop_id:
                self.db.execute(
                    """
                    UPDATE live_positions
                    SET stop_order_id = ?, stop_price = ?
                    WHERE position_id = ? AND status = 'OPEN'
                    """,
                    (restored_stop_id, float(old_stop), open_pos["position_id"]),
                )
                open_pos["stop_order_id"] = restored_stop_id
                open_pos["stop_price"] = float(old_stop)
            level = "WARN" if self._is_wash_trade_error(exc) else "ERROR"
            self._log_event(
                level=level,
                symbol=str(open_pos["symbol"]),
                event_type="stop_update_failed",
                message=f"Failed to update stop for {open_pos['symbol']}",
                data={
                    "position_id": str(open_pos["position_id"]),
                    "old_stop": float(old_stop),
                    "attempted_stop": float(new_stop),
                    "old_stop_canceled": int(old_stop_canceled),
                    "restored_stop_order_id": restored_stop_id,
                    "error": self._error_text(exc),
                },
            )
            if old_stop_canceled and not restored_stop_id:
                self.db.execute(
                    """
                    UPDATE live_positions
                    SET stop_order_id = NULL, stop_price = ?
                    WHERE position_id = ? AND status = 'OPEN'
                    """,
                    (float(old_stop), open_pos["position_id"]),
                )
                open_pos["stop_order_id"] = None
                open_pos["stop_price"] = float(old_stop)
            return

        self.db.execute(
            """
            UPDATE live_positions
            SET stop_order_id = ?, stop_price = ?
            WHERE position_id = ? AND status = 'OPEN'
            """,
            (stop_order_id, float(new_stop), open_pos["position_id"]),
        )
        open_pos["stop_order_id"] = stop_order_id
        open_pos["stop_price"] = float(new_stop)

    def _is_live_supported(self, spec: StrategySpec) -> bool:
        if spec.exit_variant in LIVE_SUPPORTED_EXIT_VARIANTS:
            return True
        if spec.exit_variant.startswith("STACK_"):
            codes = spec.exit_variant.split("_")[1:]
            return all(code in STACK_COMPONENT_CODES for code in codes)
        return False

    def _open_position_count(self) -> int:
        df = self.db.query_df("SELECT COUNT(*) AS n FROM live_positions WHERE status = 'OPEN'")
        return int(df.iloc[0]["n"]) if not df.empty else 0

    def _entries_today(self, symbol: str, session_date: str) -> int:
        df = self.db.query_df(
            "SELECT COUNT(*) AS n FROM live_positions WHERE symbol = ? AND session_date = ?",
            (symbol, session_date),
        )
        return int(df.iloc[0]["n"]) if not df.empty else 0

    def _get_open_position(self, symbol: str) -> dict | None:
        df = self.db.query_df(
            """
            SELECT *
            FROM live_positions
            WHERE symbol = ? AND status = 'OPEN'
            ORDER BY entry_ts DESC
            LIMIT 1
            """,
            (symbol,),
        )
        if df.empty:
            return None
        row = df.iloc[0].to_dict()
        row["stop_order_id"] = self._normalize_order_id(row.get("stop_order_id"))
        row["broker_order_id"] = self._normalize_order_id(row.get("broker_order_id"))
        return row

    def _get_open_positions(self, session_date: str | None = None) -> list[dict]:
        if session_date:
            df = self.db.query_df(
                """
                SELECT *
                FROM live_positions
                WHERE status = 'OPEN' AND session_date = ?
                ORDER BY symbol, entry_ts
                """,
                (session_date,),
            )
        else:
            df = self.db.query_df(
                """
                SELECT *
                FROM live_positions
                WHERE status = 'OPEN'
                ORDER BY symbol, entry_ts
                """
            )
        if df.empty:
            return []
        out: list[dict] = []
        for _, s in df.iterrows():
            row = s.to_dict()
            row["stop_order_id"] = self._normalize_order_id(row.get("stop_order_id"))
            row["broker_order_id"] = self._normalize_order_id(row.get("broker_order_id"))
            out.append(row)
        return out

    def _estimate_symbol_exit_price(self, symbol: str, fallback_price: float, timeframe_min: int = 5) -> float:
        if (not self.config.dry_run) and self.broker is not None:
            try:
                pos = self.broker.get_open_position(symbol)
                if pos:
                    for k in ("current_price", "lastday_price", "avg_entry_price"):
                        v = pos.get(k)
                        if v is None:
                            continue
                        try:
                            px = float(v)
                            if px > 0:
                                return px
                        except Exception:
                            pass
            except Exception:
                pass
        asof = self._resolve_asof_date().isoformat()
        try:
            bars_tf, _ = load_intraday_data(
                symbol=symbol,
                start=asof,
                end=asof,
                provider=self.config.data_provider,
                timeframe_min=max(1, int(timeframe_min)),
            )
            bars_tf = self._filter_to_completed_bars(bars_tf, timeframe_min=max(1, int(timeframe_min)))
            if not bars_tf.empty:
                return float(bars_tf.iloc[-1]["c"])
        except Exception:
            pass
        return float(fallback_price)

    def force_session_failsafe(
        self,
        *,
        session_date: str | None = None,
        reason: str = "SESSION_FAILSAFE",
        cancel_open_orders: bool = True,
    ) -> dict:
        open_positions = self._get_open_positions(session_date=session_date)
        ts = pd.Timestamp.now(tz=CHICAGO_TZ)
        closed_positions = 0
        close_errors = 0
        canceled_orders = 0
        symbols_closed: set[str] = set()

        for open_pos in open_positions:
            symbol = str(open_pos["symbol"])
            side = str(open_pos["side"])
            exit_price = self._estimate_symbol_exit_price(
                symbol,
                float(open_pos["entry_price"]),
                timeframe_min=int(open_pos.get("timeframe_min") or 5),
            )
            send_broker_exit = symbol not in symbols_closed
            try:
                closed_ok = self._close_position(
                    open_pos=open_pos,
                    side=side,
                    exit_price=exit_price,
                    reason=reason,
                    ts=ts,
                    send_broker_exit=send_broker_exit,
                )
                if closed_ok:
                    closed_positions += 1
                else:
                    close_errors += 1
            except Exception as exc:
                close_errors += 1
                self._log_event(
                    level="ERROR",
                    symbol=symbol,
                    event_type="failsafe_close_failed",
                    message=f"Failsafe close failed for {symbol}",
                    data={"reason": reason, "error": self._error_text(exc)},
                )
            symbols_closed.add(symbol)

        if cancel_open_orders and (not self.config.dry_run) and self.broker is not None:
            symbols = sorted({str(s).strip().upper() for s in self.config.symbols if str(s).strip()})
            for symbol in symbols:
                try:
                    open_orders = self.broker.list_open_orders(symbol=symbol) or []
                except Exception as exc:
                    self._log_event(
                        level="WARN",
                        symbol=symbol,
                        event_type="failsafe_order_scan_failed",
                        message=f"Failsafe open-order scan failed for {symbol}",
                        data={"reason": reason, "error": self._error_text(exc)},
                    )
                    continue
                for o in open_orders:
                    oid = self._normalize_order_id(o.get("id"))
                    if not oid:
                        continue
                    try:
                        self.broker.cancel_order(oid)
                        canceled_orders += 1
                    except Exception as exc:
                        self._log_event(
                            level="WARN",
                            symbol=symbol,
                            event_type="failsafe_order_cancel_failed",
                            message=f"Failsafe cancel failed for {symbol} order {oid}",
                            data={"reason": reason, "error": self._error_text(exc)},
                        )

            # One more broker flatten sweep to handle partials near EOD.
            for symbol in symbols:
                if self._attempt_force_flatten_symbol(symbol=symbol, attempts=3, sleep_seconds=0.75):
                    continue
                close_errors += 1
                broker_pos = self.broker.get_open_position(symbol)
                self._log_event(
                    level="ERROR",
                    symbol=symbol,
                    event_type="failsafe_residual_position",
                    message=f"Residual position remains for {symbol} after failsafe",
                    data={
                        "reason": reason,
                        "broker_side": str((broker_pos or {}).get("side") or ""),
                        "broker_qty": str((broker_pos or {}).get("qty") or ""),
                    },
                )

        if closed_positions > 0 or canceled_orders > 0 or close_errors > 0:
            self._log_event(
                level="INFO" if close_errors == 0 else "WARN",
                symbol=None,
                event_type="session_failsafe_summary",
                message=f"ORB failsafe {reason}: closed={closed_positions}, canceled_orders={canceled_orders}, errors={close_errors}",
                data={
                    "session_date": session_date,
                    "reason": reason,
                    "closed_positions": int(closed_positions),
                    "canceled_orders": int(canceled_orders),
                    "close_errors": int(close_errors),
                },
            )

        return {
            "session_date": session_date,
            "reason": reason,
            "closed_positions": int(closed_positions),
            "canceled_orders": int(canceled_orders),
            "close_errors": int(close_errors),
        }

    def _account_equity(self) -> float:
        if self.config.dry_run:
            return float(self.config.default_equity)
        account = self.broker.get_account()
        return float(account.get("equity", self.config.default_equity))

    def _available_long_qty(self, symbol: str) -> int:
        if self.config.dry_run or self.broker is None:
            return 0
        pos = self.broker.get_open_position(symbol)
        if not pos:
            return 0
        side = str(pos.get("side", "")).lower()
        if side != "long":
            return 0
        try:
            qty = int(abs(float(pos.get("qty", 0.0))))
        except Exception:
            qty = 0
        return max(0, qty)

    @staticmethod
    def _signed_qty_from_position(pos: dict | None) -> int:
        if not pos:
            return 0
        try:
            qty = int(abs(float(pos.get("qty", 0.0))))
        except Exception:
            return 0
        side = str(pos.get("side", "")).lower()
        if side == "long":
            return qty
        if side == "short":
            return -qty
        return 0

    def _wait_for_order_to_leave_open_book(
        self,
        *,
        symbol: str,
        order_id: str | None,
        timeout_seconds: float = 8.0,
        poll_seconds: float = 0.5,
    ) -> bool:
        if self.config.dry_run or self.broker is None:
            return True
        oid = str(order_id or "").strip()
        if not oid:
            return True
        deadline = time_module.time() + max(0.1, float(timeout_seconds))
        while time_module.time() < deadline:
            try:
                open_orders = self.broker.list_open_orders(symbol=symbol) or []
                is_open = any(str(o.get("id") or "").strip() == oid for o in open_orders)
                if not is_open:
                    return True
            except Exception:
                pass
            time_module.sleep(max(0.1, float(poll_seconds)))
        return False

    def _wait_for_symbol_flat(self, *, symbol: str, timeout_seconds: float = 8.0, poll_seconds: float = 0.5) -> bool:
        if self.config.dry_run or self.broker is None:
            return True
        deadline = time_module.time() + max(0.1, float(timeout_seconds))
        while time_module.time() < deadline:
            try:
                pos = self.broker.get_open_position(symbol)
                if pos is None or self._signed_qty_from_position(pos) == 0:
                    return True
            except Exception:
                pass
            time_module.sleep(max(0.1, float(poll_seconds)))
        return False

    def _attempt_force_flatten_symbol(self, *, symbol: str, attempts: int = 3, sleep_seconds: float = 0.75) -> bool:
        if self.config.dry_run or self.broker is None:
            return True
        if self._wait_for_symbol_flat(symbol=symbol, timeout_seconds=0.3, poll_seconds=0.1):
            return True
        for _ in range(max(1, int(attempts))):
            try:
                self.broker.close_position(symbol)
            except Exception:
                pass
            time_module.sleep(max(0.1, float(sleep_seconds)))
            if self._wait_for_symbol_flat(symbol=symbol, timeout_seconds=1.0, poll_seconds=0.2):
                return True
        return False

    def _restore_symbol_exposure(self, *, symbol: str, target_signed_qty: int) -> dict:
        if self.config.dry_run or self.broker is None:
            return {"restored": False, "reason": "dry_run_or_no_broker"}
        current_pos = self.broker.get_open_position(symbol)
        current_signed = self._signed_qty_from_position(current_pos)
        delta = int(target_signed_qty) - int(current_signed)
        if delta == 0:
            return {
                "restored": True,
                "target_signed_qty": int(target_signed_qty),
                "before_signed_qty": int(current_signed),
                "after_signed_qty": int(current_signed),
            }
        side = "buy" if delta > 0 else "sell"
        qty = abs(int(delta))
        oid = f"orb-restore-{symbol}-{uuid.uuid4().hex[:16]}"
        try:
            self.broker.submit_market_order(symbol=symbol, side=side, qty=qty, client_order_id=oid)
            time_module.sleep(0.5)
            after_pos = self.broker.get_open_position(symbol)
            after_signed = self._signed_qty_from_position(after_pos)
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
                "error": self._error_text(exc),
            }

    def _record_missed_trade(
        self,
        *,
        symbol: str,
        strategy_id: str,
        signal: dict,
        planned_qty: int,
        reason: str,
        extra: dict | None = None,
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        signal_ts = str(signal.get("entry_ts") or "")
        try:
            session_date = pd.to_datetime(signal_ts).tz_convert(CHICAGO_TZ).strftime("%Y-%m-%d")
        except Exception:
            session_date = None
        payload = {
            "strategy_id": strategy_id,
            "or_high": signal.get("or_high"),
            "or_low": signal.get("or_low"),
            "or_width": signal.get("or_width"),
        }
        if extra:
            payload.update(extra)
        self.db.execute(
            """
            INSERT INTO missed_trades
            (
                miss_id, created_at, agent, symbol, session_date, strategy_id, side,
                signal_ts, entry_price, stop_price, risk, planned_qty, reason, data_json
            )
            VALUES (?, ?, 'ORB', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                ts,
                symbol,
                session_date,
                strategy_id,
                str(signal.get("side") or ""),
                signal_ts,
                float(signal.get("entry_price") or 0.0),
                float(signal.get("stop_price") or 0.0),
                float(signal.get("risk") or 0.0),
                int(planned_qty),
                reason,
                json.dumps(payload, sort_keys=True),
            ),
        )

    def _claim_signal_lock(self, *, symbol: str, strategy_id: str, signal: dict) -> bool:
        entry_ts = str(signal.get("entry_ts") or "")
        side = str(signal.get("side") or "")
        if not entry_ts or not side:
            return True
        try:
            session_date = pd.to_datetime(entry_ts, utc=True).tz_convert(CHICAGO_TZ).strftime("%Y-%m-%d")
        except Exception:
            session_date = self._resolve_asof_date().isoformat()
        with self.db.connect() as conn:
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO live_signal_locks
                (signal_id, created_at, session_date, symbol, strategy_id, side, entry_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    datetime.now(timezone.utc).isoformat(),
                    session_date,
                    symbol,
                    strategy_id,
                    side,
                    entry_ts,
                ),
            )
            return conn.total_changes > before

    def _resolve_asof_date(self) -> date:
        if self.config.asof_date:
            return pd.Timestamp(self.config.asof_date).date()
        return datetime.now(tz=CHICAGO_TZ).date()

    def _previous_session_close(self, symbol: str, session_date: date) -> float | None:
        cache_key = (str(symbol).upper(), session_date.isoformat())
        if cache_key in self._prev_close_cache:
            return self._prev_close_cache[cache_key]
        # Pull a recent tail and resolve in pandas to avoid timestamp-format quirks in sqlite comparisons.
        df = self.db.query_df(
            """
            SELECT ts, c
            FROM bars_5m
            WHERE symbol = ?
            ORDER BY ts DESC
            LIMIT 4000
            """,
            (str(symbol).upper(),),
        )
        if df.empty:
            self._prev_close_cache[cache_key] = None
            return None
        ts = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        vals = pd.to_numeric(df["c"], errors="coerce")
        sess_start_utc = pd.Timestamp(session_date).tz_localize(CHICAGO_TZ).tz_convert("UTC")
        mask = ts < sess_start_utc
        if not mask.any():
            self._prev_close_cache[cache_key] = None
            return None
        close = float(vals.loc[mask].iloc[0])
        if not math.isfinite(close) or close <= 0:
            self._prev_close_cache[cache_key] = None
            return None
        self._prev_close_cache[cache_key] = close
        return close

    @staticmethod
    def _filter_to_completed_bars(bars: pd.DataFrame, timeframe_min: int) -> pd.DataFrame:
        if bars.empty:
            return bars
        work = bars.copy()
        ts_ct = pd.to_datetime(work["ts"], utc=True).dt.tz_convert(CHICAGO_TZ)
        now_ct = pd.Timestamp.now(tz=CHICAGO_TZ)
        tf = max(1, int(timeframe_min))
        last_completed_close = now_ct.floor(f"{tf}min")
        latest_completed_open = last_completed_close - pd.Timedelta(minutes=tf)
        return work.loc[ts_ct <= latest_completed_open].copy()

    def _log_event(self, level: str, symbol: str | None, event_type: str, message: str, data: dict | None = None) -> None:
        self.db.execute(
            """
            INSERT INTO live_events (event_id, created_at, level, symbol, event_type, message, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                level,
                symbol,
                event_type,
                message,
                json.dumps(data or {}, sort_keys=True),
            ),
        )
        self._notify_discord(level=level, symbol=symbol, event_type=event_type, message=message, data=data or {})

    def publish_eod_trade_report(self, session_date: str) -> dict:
        out_dir = Path("artifacts/reports/orb_live")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"orb_eod_trades_{session_date}.csv"

        trades = self.db.query_df(
            """
            SELECT
                session_date, symbol, strategy_id, side, qty,
                entry_ts, exit_ts,
                entry_price, exit_price,
                initial_stop_price, final_stop_price,
                risk, pnl, pnl_pct, r_mult, exit_reason,
                timeframe_min, exit_variant, trade_limit_1d, long_cutoff_ct, data_provider
            FROM live_trades
            WHERE session_date = ?
            ORDER BY exit_ts, symbol
            """,
            (session_date,),
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
                        "initial_stop_price": 0.0,
                        "final_stop_price": 0.0,
                        "risk": 0.0,
                        "pnl": 0.0,
                        "pnl_pct": 0.0,
                        "r_mult": 0.0,
                        "exit_reason": "NO_TRADES",
                        "timeframe_min": "",
                        "exit_variant": "",
                        "trade_limit_1d": "",
                        "long_cutoff_ct": "",
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
                work[f"{col}_ct"] = dt.dt.tz_convert(CHICAGO_TZ).dt.strftime("%Y-%m-%d %H:%M:%S%z")

            wins = int((pd.to_numeric(work["pnl"], errors="coerce").fillna(0.0) > 0).sum())
            n = int(len(work))
            day_total_pnl = float(pd.to_numeric(work["pnl"], errors="coerce").fillna(0.0).sum())
            day_avg_r = float(pd.to_numeric(work["r_mult"], errors="coerce").fillna(0.0).mean())
            day_win_rate = float((wins / n) if n > 0 else 0.0)

            work["day_trades"] = ""
            work["day_wins"] = ""
            work["day_win_rate"] = ""
            work["day_total_pnl"] = ""
            work["day_avg_r"] = ""
            work = work.rename(columns={"entry_ts_ct": "entry_ts_ct", "exit_ts_ct": "exit_ts_ct"})
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
                "initial_stop_price",
                "final_stop_price",
                "risk",
                "pnl",
                "pnl_pct",
                "r_mult",
                "exit_reason",
                "timeframe_min",
                "exit_variant",
                "trade_limit_1d",
                "long_cutoff_ct",
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
        webhook = (self.config.discord_webhook_url or "").strip()
        if self.config.discord_enabled and webhook:
            posted = self._post_discord_csv_file(
                webhook=webhook,
                file_path=out_path,
                content=f"📊 ORB EOD trade report {session_date}",
            )

        self._log_event(
            level="INFO",
            symbol=None,
            event_type="eod_trade_report",
            message=f"ORB EOD trade report generated for {session_date}",
            data={"path": str(out_path), "posted_to_discord": bool(posted)},
        )
        return {"path": str(out_path), "posted_to_discord": bool(posted)}

    @staticmethod
    def _post_discord_csv_file(*, webhook: str, file_path: Path, content: str) -> bool:
        try:
            payload = {"content": content}
            with file_path.open("rb") as f:
                files = {"file": (file_path.name, f, "text/csv")}
                resp = requests.post(webhook, data=payload, files=files, timeout=20)
            return 200 <= resp.status_code < 300
        except Exception:
            return False

    def _notify_discord(self, level: str, symbol: str | None, event_type: str, message: str, data: dict) -> None:
        if not self.config.discord_enabled:
            return
        webhook = (self.config.discord_webhook_url or "").strip()
        if not webhook:
            return
        alert_events = {
            "entry_signal_detected",
            "entry_opened",
            "position_closed",
            "strategy_fallback",
            "stale_signal_skipped",
            "short_blocked_no_inventory",
            "short_not_available",
            "entry_submit_failed",
            "exit_partial_unfilled",
            "stop_attach_failed_flattened",
            "protective_stop_attached",
            "protective_stop_attach_failed",
            "stop_update_failed",
            "session_failsafe_summary",
            "failsafe_close_failed",
            "failsafe_order_cancel_failed",
            "failsafe_residual_position",
        }
        if level not in {"WARN", "ERROR"} and event_type not in alert_events:
            return

        sym = symbol or str(data.get("symbol") or "")
        side = str(data.get("side") or "").upper()
        side_emoji = "🟢" if side == "LONG" else ("🔴" if side == "SHORT" else "⚪")
        level_emoji = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "🚨"}.get(level, "ℹ️")
        event_emoji = {
            "entry_signal_detected": "🧭",
            "entry_opened": "✅",
            "position_closed": "🏁",
            "strategy_fallback": "🔁",
            "stale_signal_skipped": "🕒",
            "short_blocked_no_inventory": "⛔",
            "short_not_available": "🚫",
            "entry_submit_failed": "❌",
            "exit_partial_unfilled": "🧱",
            "stop_attach_failed_flattened": "🛑",
            "protective_stop_attached": "🛡️",
            "protective_stop_attach_failed": "🚨",
            "stop_update_failed": "⚠️",
            "failsafe_residual_position": "🚨",
        }.get(event_type, "📣")
        ts = datetime.now(tz=CHICAGO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        strategy_id = str(data.get("strategy_id") or "")
        tf = data.get("timeframe_min")
        exit_variant = str(data.get("exit_variant") or "")
        trade_limit = data.get("trade_limit_1d")
        cutoff_ct = str(data.get("long_cutoff_ct") or "")
        if strategy_id and (not exit_variant or tf is None):
            try:
                spec = parse_strategy_id(strategy_id)
                if not exit_variant:
                    exit_variant = spec.exit_variant
                if tf is None:
                    tf = spec.timeframe_min
                if trade_limit is None:
                    trade_limit = int(spec.trade_limit_1d)
                if not cutoff_ct:
                    cutoff_ct = spec.long_cutoff.strftime("%H:%M") if spec.long_cutoff else "NONE"
            except Exception:
                pass

        lines: list[str] = [
            f"{level_emoji} **[ORB] {event_type}** {event_emoji}",
            f"🕒 **Time:** {ts}",
            f"📌 **Symbol:** {sym or '-'} | **Level:** {level}",
            f"💬 **Message:** {message}",
        ]
        if side:
            lines.append(f"🧭 **Side:** {side_emoji} {side}")
        if strategy_id:
            lines.append(f"🧠 **Strategy:** `{strategy_id}`")
        if tf is not None or exit_variant:
            tf_txt = f"{tf}m" if tf is not None else "-"
            lines.append(
                f"⚙️ **Execution Plan:** TF={tf_txt} | Exit={exit_variant or '-'} | "
                f"Limit1D={trade_limit if trade_limit is not None else '-'} | Cutoff={cutoff_ct or '-'} CT"
            )

        entry_bits: list[str] = []
        if "entry_ts" in data:
            entry_bits.append(f"ts={data['entry_ts']}")
        if "qty" in data:
            entry_bits.append(f"qty={data['qty']}")
        if "entry_price" in data:
            entry_bits.append(f"entry={data['entry_price']}")
        if "stop_price" in data:
            entry_bits.append(f"stop={data['stop_price']}")
        if "risk" in data:
            entry_bits.append(f"risk={data['risk']}")
        if entry_bits:
            lines.append(f"💼 **Trade:** {' | '.join(entry_bits)}")
        if "signal_source" in data:
            lines.append(f"🧩 **Signal Source:** {data.get('signal_source')}")
        if any(k in data for k in ("gap_pct", "dist0", "dist1")):
            lines.append(
                "🕳️ **Gap Add-on:** "
                f"gap={data.get('gap_pct', '-')} | dist0={data.get('dist0', '-')} | dist1={data.get('dist1', '-')}"
            )

        if any(k in data for k in ("or_high", "or_low", "or_width")):
            lines.append(
                "📏 **OR:** "
                f"high={data.get('or_high', '-')} | low={data.get('or_low', '-')} | width={data.get('or_width', '-')}"
            )
        if any(k in data for k in ("stop_status", "stop_order_attached", "broker_order_id", "stop_order_id")):
            lines.append(
                "🛡️ **Protection:** "
                f"status={data.get('stop_status', '-')} | attached={data.get('stop_order_attached', '-')} | "
                f"broker_order={data.get('broker_order_id', '-')} | stop_order={data.get('stop_order_id', '-')}"
            )
        if any(k in data for k in ("exit_price", "reason", "pnl", "r_mult")):
            lines.append(
                "🏁 **Exit:** "
                f"price={data.get('exit_price', '-')} | reason={data.get('reason', '-')} | "
                f"pnl={data.get('pnl', '-')} | R={data.get('r_mult', '-')}"
            )
        if "error" in data:
            lines.append(f"🧯 **Error:** {data.get('error')}")
        if "position_id" in data:
            lines.append(f"🆔 **Position:** {data.get('position_id')}")

        text = "\n".join(lines)
        try:
            requests.post(webhook, json={"content": text[:1800]}, timeout=10)
        except Exception:
            pass

    def _ensure_stop_order(self, open_pos: dict, side: str, stop_price: float) -> None:
        if self.config.dry_run:
            return
        if self._normalize_order_id(open_pos.get("stop_order_id")):
            return
        stop_side = "sell" if side == "LONG" else "buy"
        client_oid = f"orb-stop-recover-{open_pos['symbol']}-{uuid.uuid4().hex[:16]}"
        try:
            order = self.broker.submit_stop_order(
                symbol=str(open_pos["symbol"]),
                side=stop_side,
                qty=int(open_pos["qty"]),
                stop_price=float(stop_price),
                client_order_id=client_oid,
            )
            stop_order_id = self._normalize_order_id(order.get("id"))
            self.db.execute(
                """
                UPDATE live_positions
                SET stop_order_id = ?, stop_price = ?
                WHERE position_id = ? AND status = 'OPEN'
                """,
                (stop_order_id, float(stop_price), open_pos["position_id"]),
            )
            open_pos["stop_order_id"] = stop_order_id
            open_pos["stop_price"] = float(stop_price)
            self._log_event(
                level="INFO",
                symbol=str(open_pos["symbol"]),
                event_type="protective_stop_attached",
                message=f"Attached deferred protective stop for {open_pos['symbol']}",
                data={
                    "position_id": str(open_pos["position_id"]),
                    "stop_price": float(stop_price),
                    "stop_order_id": stop_order_id,
                },
            )
        except Exception as exc:
            level = "WARN" if self._is_wash_trade_error(exc) else "ERROR"
            self._log_event(
                level=level,
                symbol=str(open_pos["symbol"]),
                event_type="protective_stop_attach_failed",
                message=f"Failed to attach protective stop for {open_pos['symbol']}",
                data={
                    "position_id": str(open_pos["position_id"]),
                    "stop_price": float(stop_price),
                    "error": self._error_text(exc),
                },
            )

    @staticmethod
    def _normalize_order_id(order_id: object) -> str | None:
        if order_id is None:
            return None
        try:
            if pd.isna(order_id):
                return None
        except Exception:
            pass
        val = str(order_id).strip()
        if not val:
            return None
        if val.lower() in {"nan", "none", "null"}:
            return None
        return val

    @staticmethod
    def _is_wash_trade_error(exc: Exception) -> bool:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            if int(exc.response.status_code) == 403:
                txt = (exc.response.text or "").lower()
                return "wash trade" in txt
        return "wash trade" in str(exc).lower()

    @staticmethod
    def _is_short_unavailable_error(exc: Exception) -> bool:
        text = ""
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            text = (exc.response.text or "").lower()
        if not text:
            text = str(exc).lower()
        needles = (
            "not shortable",
            "insufficient qty available for short sale",
            "cannot be sold short",
            "short sale",
        )
        return any(n in text for n in needles)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            text = (exc.response.text or "").strip()
            if text:
                return text[:1000]
        return str(exc)[:1000]


def _prepare_frame(bars_5m: pd.DataFrame) -> pd.DataFrame:
    frame = bars_5m.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values("ts").reset_index(drop=True)
    frame["ts_local"] = frame["ts"].dt.tz_convert(CHICAGO_TZ)
    frame["session_date"] = frame["ts_local"].dt.strftime("%Y-%m-%d")
    frame["time_local"] = frame["ts_local"].dt.time
    frame["ema20"] = frame["c"].ewm(span=20, adjust=False).mean()
    return frame


def _find_latest_entry_signal(
    frame: pd.DataFrame,
    spec: StrategySpec,
    side_mode: str,
    *,
    gap_entry_enabled: bool = False,
    gap_timeframe_min: int = 15,
    gap_apply_on_limit1: bool = False,
    gap_threshold: float = 0.0015,
    gap_dist_min: float = 0.001,
    gap_dist_max: float = 0.012,
    gap_require_close_compare: bool = True,
    gap_require_body_direction: bool = True,
    prev_session_close: float | None = None,
) -> dict | None:
    if frame.empty:
        return None

    # Work strictly on the latest session in the frame.
    session = str(frame.iloc[-1]["session_date"])
    day = frame[frame["session_date"] == session].copy().reset_index(drop=True)
    if day.empty:
        return None

    opening = day[(day["time_local"] >= SESSION_START) & (day["time_local"] < OR_END)].copy()
    min_bars = 6 if spec.timeframe_min == 5 else 2
    if opening.shape[0] < min_bars:
        return None

    or_high = float(opening["h"].max())
    or_low = float(opening["l"].min())
    post = day[(day["time_local"] >= OR_END) & (day["time_local"] <= time(14, 45))].copy().reset_index(drop=True)

    # Base OR confirmation signal.
    if post.shape[0] >= 3:
        i = len(post) - 1
        bar_prev2 = post.iloc[i - 2]
        bar_prev = post.iloc[i - 1]
        bar_cur = post.iloc[i]

        long_confirm = bool(bar_prev["c"] > or_high and bar_cur["c"] > or_high and bar_prev2["c"] <= or_high)
        short_confirm = bool(bar_prev["c"] < or_low and bar_cur["c"] < or_low and bar_prev2["c"] >= or_low)

        if side_mode == "long_only":
            short_confirm = False
        if side_mode == "short_only":
            long_confirm = False

        if long_confirm or short_confirm:
            side = "LONG" if long_confirm else "SHORT"
            if side == "LONG" and spec.long_cutoff is not None and bar_cur["time_local"] > spec.long_cutoff:
                return None

            if side == "LONG":
                entry_price = float(bar_cur["c"])
                stop_price = min(float(bar_prev["l"]), float(bar_cur["l"])) - 0.01
                risk = entry_price - stop_price
                if risk <= 0:
                    return None
            else:
                entry_price = float(bar_cur["c"])
                stop_price = max(float(bar_prev["h"]), float(bar_cur["h"])) + 0.01
                risk = stop_price - entry_price
                if risk <= 0:
                    return None

            return {
                "side": side,
                "entry_ts": pd.Timestamp(bar_cur["ts"]).strftime("%Y-%m-%d %H:%M:%S%z"),
                "entry_price": entry_price,
                "stop_price": stop_price,
                "risk": risk,
                "or_high": or_high,
                "or_low": or_low,
                "or_width": (or_high - or_low),
                "entry_bar_open": float(bar_cur["o"]),
                "entry_bar_high": float(bar_cur["h"]),
                "entry_bar_low": float(bar_cur["l"]),
                "entry_bar_close": float(bar_cur["c"]),
                "prev_bar_close": float(bar_prev["c"]),
                "prev2_bar_close": float(bar_prev2["c"]),
                "signal_source": "ORB_CONFIRM",
            }

    # Optional gap add-on signal (evaluated when base OR confirm is absent).
    if not gap_entry_enabled:
        return None
    if int(spec.timeframe_min) != int(gap_timeframe_min):
        return None
    if int(spec.trade_limit_1d) == 1 and not gap_apply_on_limit1:
        return None

    return _find_gap_addon_signal(
        opening=opening.sort_values("ts").reset_index(drop=True),
        or_high=or_high,
        or_low=or_low,
        side_mode=side_mode,
        long_cutoff=spec.long_cutoff,
        prev_session_close=prev_session_close,
        gap_threshold=float(gap_threshold),
        dist_min=float(gap_dist_min),
        dist_max=float(gap_dist_max),
        require_close_compare=bool(gap_require_close_compare),
        require_body_direction=bool(gap_require_body_direction),
    )


def _find_gap_addon_signal(
    *,
    opening: pd.DataFrame,
    or_high: float,
    or_low: float,
    side_mode: str,
    long_cutoff: time | None,
    prev_session_close: float | None,
    gap_threshold: float,
    dist_min: float,
    dist_max: float,
    require_close_compare: bool,
    require_body_direction: bool,
) -> dict | None:
    if opening.shape[0] < 2:
        return None
    if prev_session_close is None or not math.isfinite(prev_session_close) or prev_session_close <= 0:
        return None

    b0 = opening.iloc[0]
    b1 = opening.iloc[1]
    gap_pct = (float(b0["o"]) / float(prev_session_close)) - 1.0
    if abs(gap_pct) < gap_threshold:
        return None

    # Seed 15m EMA20 distances from previous close to preserve prior-session context.
    alpha = 2.0 / (20.0 + 1.0)
    ema0 = float(prev_session_close) + alpha * (float(b0["c"]) - float(prev_session_close))
    ema1 = ema0 + alpha * (float(b1["c"]) - ema0)
    if ema0 <= 0 or ema1 <= 0:
        return None
    dist0 = (float(b0["c"]) / ema0) - 1.0
    dist1 = (float(b1["c"]) / ema1) - 1.0

    side = "LONG" if gap_pct > 0 else "SHORT"
    if side_mode == "long_only" and side != "LONG":
        return None
    if side_mode == "short_only" and side != "SHORT":
        return None

    if side == "LONG":
        if long_cutoff is not None and b1["time_local"] > long_cutoff:
            return None
        if dist0 < dist_min or dist1 < dist_min:
            return None
        if abs(dist0) > dist_max or abs(dist1) > dist_max:
            return None
        if require_close_compare and float(b1["c"]) <= float(b0["c"]):
            return None
        if require_body_direction and not (float(b0["c"]) > float(b0["o"]) and float(b1["c"]) > float(b1["o"])):
            return None
        entry_price = float(b1["c"])
        stop_price = min(float(b0["l"]), float(b1["l"])) - 0.01
        risk = entry_price - stop_price
        if risk <= 0:
            return None
    else:
        if dist0 > -dist_min or dist1 > -dist_min:
            return None
        if abs(dist0) > dist_max or abs(dist1) > dist_max:
            return None
        if require_close_compare and float(b1["c"]) >= float(b0["c"]):
            return None
        if require_body_direction and not (float(b0["c"]) < float(b0["o"]) and float(b1["c"]) < float(b1["o"])):
            return None
        entry_price = float(b1["c"])
        stop_price = max(float(b0["h"]), float(b1["h"])) + 0.01
        risk = stop_price - entry_price
        if risk <= 0:
            return None

    return {
        "side": side,
        "entry_ts": pd.Timestamp(b1["ts"]).strftime("%Y-%m-%d %H:%M:%S%z"),
        "entry_price": entry_price,
        "stop_price": stop_price,
        "risk": risk,
        "or_high": float(or_high),
        "or_low": float(or_low),
        "or_width": float(or_high - or_low),
        "entry_bar_open": float(b1["o"]),
        "entry_bar_high": float(b1["h"]),
        "entry_bar_low": float(b1["l"]),
        "entry_bar_close": float(b1["c"]),
        "prev_bar_close": float(b0["c"]),
        "prev2_bar_close": float(prev_session_close),
        "signal_source": "GAP_ADDON",
        "gap_pct": float(gap_pct),
        "dist0": float(dist0),
        "dist1": float(dist1),
    }


def _compute_position_size(
    equity: float,
    risk_pct: float,
    max_notional_pct: float,
    max_notional_dollars: float,
    entry_price: float,
    stop_price: float,
) -> int:
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return 0
    risk_dollars = max(0.0, equity * risk_pct)
    qty_risk = math.floor(risk_dollars / risk_per_share)
    max_notional = max(0.0, equity * max_notional_pct)
    qty_notional = math.floor(max_notional / entry_price) if entry_price > 0 else 0
    qty_abs_cap = math.floor(max(0.0, max_notional_dollars) / entry_price) if entry_price > 0 else 0
    qty = min(qty_risk, qty_notional, qty_abs_cap)
    return max(0, int(qty))


def _evaluate_exit_incremental(
    *,
    side: str,
    exit_variant: str,
    bar_time: time,
    high: float,
    low: float,
    close: float,
    ema: float,
    trail: float,
    entry_price: float,
    risk: float,
    or_high: float,
    or_low: float,
    progress_hit: bool,
    be_armed: bool,
    bars_since_entry: int,
    timeframe_min: int,
) -> dict:
    components = _components_for_variant(exit_variant)
    new_trail = trail

    if "TIME_STOP_NO_PROGRESS" in components:
        max_bars = NO_PROGRESS_BARS_BY_TF.get(timeframe_min, 4)
        if bars_since_entry >= max_bars and not progress_hit:
            return {"should_exit": True, "exit_price": close, "reason": "TIME_STOP_NO_PROGRESS", "trail": new_trail, "new_stop": False}

    if "OR_REENTRY_FAIL" in components:
        reentry = close <= or_high if side == "LONG" else close >= or_low
        if reentry:
            return {"should_exit": True, "exit_price": close, "reason": "OR_REENTRY_FAIL", "trail": new_trail, "new_stop": False}

    if "BREAKEVEN_RATCHET" in components and be_armed:
        new_trail = max(new_trail, entry_price) if side == "LONG" else min(new_trail, entry_price)

    if exit_variant == "FIXED_2R":
        target_2r = entry_price + 2.0 * risk if side == "LONG" else entry_price - 2.0 * risk
        stop_hit = low <= new_trail if side == "LONG" else high >= new_trail
        if stop_hit:
            return {"should_exit": True, "exit_price": new_trail, "reason": "STOP", "trail": new_trail, "new_stop": False}
        target_hit = high >= target_2r if side == "LONG" else low <= target_2r
        if target_hit:
            return {"should_exit": True, "exit_price": target_2r, "reason": "TARGET_2R", "trail": new_trail, "new_stop": False}
    else:
        pre_trail = new_trail
        new_trail = max(new_trail, ema) if side == "LONG" else min(new_trail, ema)
        stop_hit = low <= new_trail if side == "LONG" else high >= new_trail
        if stop_hit:
            return {"should_exit": True, "exit_price": new_trail, "reason": "TRAIL_STOP", "trail": new_trail, "new_stop": new_trail != pre_trail}
        if bar_time >= FORCED_EXIT_TIME:
            return {"should_exit": True, "exit_price": close, "reason": "TIME_EXIT", "trail": new_trail, "new_stop": new_trail != pre_trail}
        return {"should_exit": False, "exit_price": None, "reason": None, "trail": new_trail, "new_stop": new_trail != pre_trail}

    if bar_time >= FORCED_EXIT_TIME:
        return {"should_exit": True, "exit_price": close, "reason": "TIME_EXIT", "trail": new_trail, "new_stop": False}
    return {"should_exit": False, "exit_price": None, "reason": None, "trail": new_trail, "new_stop": False}


def _components_for_variant(exit_variant: str) -> set[str]:
    if exit_variant.startswith("STACK_"):
        codes = exit_variant.split("_")[1:]
        return {STACK_COMPONENT_CODES[c] for c in codes if c in STACK_COMPONENT_CODES}
    if exit_variant in {"TIME_STOP_NO_PROGRESS", "OR_REENTRY_FAIL", "BREAKEVEN_RATCHET"}:
        return {exit_variant}
    return set()
