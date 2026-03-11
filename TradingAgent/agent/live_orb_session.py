from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
import time
import sys
import os
import atexit
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

from agent.config import CHICAGO_TZ, FORCED_EXIT_TIME, SESSION_END, SESSION_START
from agent.db import Database
from agent.live_orb import LiveOrbTrader, LiveTradeConfig


@dataclass(frozen=True)
class LiveOrbSessionConfig:
    trade_config: LiveTradeConfig
    session_calendar: str = "NYSE"
    poll_seconds: int = 5
    wait_for_open: bool = True
    dashboard: bool = True
    dashboard_min_refresh_seconds: int = 30


class LiveOrbSessionRunner:
    def __init__(self, config: LiveOrbSessionConfig):
        self.config = config
        self._lock_path = Path("artifacts/orb_paper_live.pid")
        self._lock_acquired = False
        self._last_dashboard_signature: tuple | None = None
        self._last_dashboard_render_monotonic: float = 0.0
        self._acquire_run_lock()
        self.db = Database(config.trade_config.db_path)
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass

    def run(self) -> dict:
        session_date = self._resolve_session_date()
        now_ct = datetime.now(CHICAGO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        print(
            f"[ORB_PAPER] boot at {now_ct} | session_date={session_date.isoformat()} "
            f"| calendar={self.config.session_calendar} | dashboard={self.config.dashboard}",
            flush=True,
        )
        if not self._is_session_day(session_date):
            summary = {
                "mode": "paper_live",
                "status": "not_session_day",
                "session_date": session_date.isoformat(),
                "calendar": self.config.session_calendar,
            }
            self._render_dashboard(
                status="not_session_day",
                session_date=session_date,
                cycles=0,
                last_cycle_at=None,
                next_bar_close=None,
                last_summary=None,
            )
            return summary

        session_start_dt = datetime.combine(session_date, SESSION_START, tzinfo=CHICAGO_TZ)
        session_end_dt = datetime.combine(session_date, SESSION_END, tzinfo=CHICAGO_TZ)
        forced_exit_dt = datetime.combine(session_date, FORCED_EXIT_TIME, tzinfo=CHICAGO_TZ)
        trade_cfg = replace(self.config.trade_config, asof_date=session_date.isoformat())
        trader = LiveOrbTrader(trade_cfg)

        cycles = 0
        last_cycle_at: datetime | None = None
        last_bar_close: datetime | None = None
        last_summary: dict | None = None
        forced_exit_done = False
        forced_exit_summary: dict | None = None

        while True:
            now = datetime.now(CHICAGO_TZ)

            if now > session_end_dt:
                break

            if now < session_start_dt:
                self._render_dashboard(
                    status="waiting_for_open",
                    session_date=session_date,
                    cycles=cycles,
                    last_cycle_at=last_cycle_at,
                    next_bar_close=session_start_dt + timedelta(minutes=5),
                    last_summary=last_summary,
                )
                if not self.config.wait_for_open:
                    return {
                        "mode": "paper_live",
                        "status": "before_session",
                        "session_date": session_date.isoformat(),
                        "cycles": cycles,
                    }
                time.sleep(self.config.poll_seconds)
                continue

            if (not forced_exit_done) and now >= forced_exit_dt:
                forced_exit_summary = trader.force_session_failsafe(
                    reason="SESSION_FAILSAFE_1450",
                    cancel_open_orders=True,
                )
                forced_exit_done = True

            latest_bar_close = self._latest_complete_bar_close(now, session_start_dt, session_end_dt)
            should_run_cycle = latest_bar_close is not None and (
                last_bar_close is None or latest_bar_close > last_bar_close
            )

            if should_run_cycle:
                self._render_dashboard(
                    status="cycle_running",
                    session_date=session_date,
                    cycles=cycles,
                    last_cycle_at=last_cycle_at,
                    next_bar_close=self._next_bar_close(now, session_start_dt, session_end_dt),
                    last_summary=last_summary,
                )
                last_summary = trader.run()
                cycles += 1
                last_cycle_at = now
                last_bar_close = latest_bar_close

            self._render_dashboard(
                status="running",
                session_date=session_date,
                cycles=cycles,
                last_cycle_at=last_cycle_at,
                next_bar_close=self._next_bar_close(now, session_start_dt, session_end_dt),
                last_summary=last_summary,
            )
            time.sleep(self.config.poll_seconds)

        eod_failsafe_summary = trader.force_session_failsafe(
            reason="SESSION_FAILSAFE_EOD",
            cancel_open_orders=True,
        )
        eod_trade_report = trader.publish_eod_trade_report(session_date=session_date.isoformat())
        final_summary = {
            "mode": "paper_live",
            "status": "session_complete",
            "session_date": session_date.isoformat(),
            "calendar": self.config.session_calendar,
            "cycles": cycles,
            "last_cycle_at": last_cycle_at.isoformat() if last_cycle_at else None,
            "last_trade_summary_mode": (last_summary or {}).get("mode"),
            "forced_exit_summary": forced_exit_summary,
            "eod_failsafe_summary": eod_failsafe_summary,
            "eod_trade_report": eod_trade_report,
        }
        self._render_dashboard(
            status="session_complete",
            session_date=session_date,
            cycles=cycles,
            last_cycle_at=last_cycle_at,
            next_bar_close=None,
            last_summary=last_summary,
        )
        return final_summary

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def _acquire_run_lock(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        cur_pid = os.getpid()
        if self._lock_path.exists():
            try:
                existing_pid = int(self._lock_path.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                existing_pid = 0
            if existing_pid and existing_pid != cur_pid and self._pid_alive(existing_pid):
                raise RuntimeError(
                    f"ORB paper_live already running (pid={existing_pid}). "
                    "Stop existing ORB process before starting another."
                )
        self._lock_path.write_text(str(cur_pid), encoding="utf-8")
        self._lock_acquired = True
        atexit.register(self._release_run_lock)

    def _release_run_lock(self) -> None:
        if not self._lock_acquired:
            return
        try:
            if self._lock_path.exists():
                txt = self._lock_path.read_text(encoding="utf-8").strip()
                if txt == str(os.getpid()):
                    self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        self._lock_acquired = False

    def _resolve_session_date(self) -> date:
        if self.config.trade_config.asof_date:
            return pd.Timestamp(self.config.trade_config.asof_date).date()
        return datetime.now(CHICAGO_TZ).date()

    def _is_session_day(self, session_date: date) -> bool:
        cal = mcal.get_calendar(self.config.session_calendar)
        schedule = cal.schedule(start_date=session_date.isoformat(), end_date=session_date.isoformat())
        return not schedule.empty

    @staticmethod
    def _latest_complete_bar_close(now: datetime, session_start_dt: datetime, session_end_dt: datetime) -> datetime | None:
        first_close = session_start_dt + timedelta(minutes=5)
        if now < first_close:
            return None
        elapsed_mins = int((now - session_start_dt).total_seconds() // 60)
        close_mins = (elapsed_mins // 5) * 5
        ts = session_start_dt + timedelta(minutes=close_mins)
        if ts > session_end_dt:
            return session_end_dt
        return ts

    @staticmethod
    def _next_bar_close(now: datetime, session_start_dt: datetime, session_end_dt: datetime) -> datetime | None:
        first_close = session_start_dt + timedelta(minutes=5)
        if now < first_close:
            return first_close
        elapsed_mins = int((now - session_start_dt).total_seconds() // 60)
        next_close_mins = ((elapsed_mins // 5) + 1) * 5
        ts = session_start_dt + timedelta(minutes=next_close_mins)
        if ts > session_end_dt:
            return None
        return ts

    def _render_dashboard(
        self,
        *,
        status: str,
        session_date: date,
        cycles: int,
        last_cycle_at: datetime | None,
        next_bar_close: datetime | None,
        last_summary: dict | None,
    ) -> None:
        if not self.config.dashboard:
            return

        open_positions = self._scalar("SELECT COUNT(*) FROM live_positions WHERE status='OPEN'")
        entries_today = self._scalar(
            "SELECT COUNT(*) FROM live_positions WHERE session_date = ?",
            (session_date.isoformat(),),
        )
        closed_today = self._scalar(
            "SELECT COUNT(*) FROM live_trades WHERE session_date = ?",
            (session_date.isoformat(),),
        )
        recent_event = self._recent_event_text()
        last_actions = self._last_actions(last_summary)
        now_ct = datetime.now(CHICAGO_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        last_cycle = last_cycle_at.strftime("%H:%M:%S") if last_cycle_at else "-"
        next_close = next_bar_close.strftime("%H:%M:%S") if next_bar_close else "-"
        symbols = ",".join(self.config.trade_config.symbols)
        dry_run = self.config.trade_config.dry_run
        variants = self._active_variants_text(session_date)
        signature = (
            status,
            cycles,
            last_cycle,
            next_close,
            open_positions,
            entries_today,
            closed_today,
            recent_event,
            last_actions,
            variants,
        )
        now_mono = time.monotonic()
        force_render = status in {"not_session_day", "session_complete", "cycle_running"}
        refresh_elapsed = now_mono - self._last_dashboard_render_monotonic
        if (
            not force_render
            and signature == self._last_dashboard_signature
            and refresh_elapsed < float(max(1, self.config.dashboard_min_refresh_seconds))
        ):
            return

        print("\033[2J\033[H", end="")
        print("ORB PAPER LIVE DASHBOARD")
        print("=" * 72)
        print(f"Now: {now_ct} | Session Date: {session_date.isoformat()} | Status: {status}")
        print(f"Calendar: {self.config.session_calendar} | DryRun: {dry_run} | Provider: {self.config.trade_config.data_provider}")
        print(f"Last Cycle: {last_cycle} | Next Bar Close: {next_close} | Cycles: {cycles}")
        print(f"Open Positions: {open_positions} | Entries Today: {entries_today} | Closed Trades Today: {closed_today}")
        print(f"Symbols: {symbols}")
        print(f"Variants: {variants}")
        print("-" * 72)
        print(f"Last Actions: {last_actions}")
        print(f"Recent Event: {recent_event}")
        print("=" * 72, flush=True)
        self._last_dashboard_signature = signature
        self._last_dashboard_render_monotonic = now_mono

    def _scalar(self, sql: str, params: tuple = ()) -> int:
        df = self.db.query_df(sql, params)
        if df.empty:
            return 0
        return int(df.iloc[0, 0] or 0)

    def _recent_event_text(self) -> str:
        df = self.db.query_df(
            """
            SELECT created_at, level, COALESCE(symbol, '-') AS symbol, event_type
            FROM live_events
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if df.empty:
            return "-"
        row = df.iloc[0]
        ts = str(row["created_at"])
        return f"{ts} | {row['level']} | {row['symbol']} | {row['event_type']}"

    @staticmethod
    def _last_actions(summary: dict | None) -> str:
        if not summary:
            return "-"
        events = summary.get("events", [])
        if not events:
            return "-"
        actions: list[str] = []
        for e in events:
            status = str(e.get("status", ""))
            symbol = str(e.get("symbol", ""))
            if status in {"entry_opened", "position_closed", "position_managed", "strategy_fallback"}:
                bit = f"{symbol}:{status}"
                if e.get("reason"):
                    bit += f"({e['reason']})"
                actions.append(bit)
        if not actions:
            return "No trade events"
        return " | ".join(actions[:8])

    def _active_variants_text(self, session_date: date) -> str:
        symbols = [str(s).strip().upper() for s in self.config.trade_config.symbols if str(s).strip()]
        default_sid = str(self.config.trade_config.default_strategy_id)
        if not symbols:
            return default_sid

        placeholders = ",".join(["?"] * len(symbols))
        query = f"""
            SELECT s.symbol, s.strategy_id
            FROM strategy_selections s
            JOIN (
                SELECT symbol, MAX(asof_date) AS max_asof
                FROM strategy_selections
                WHERE is_active = 1
                  AND frequency = ?
                  AND side_mode = ?
                  AND asof_date <= ?
                  AND symbol IN ({placeholders})
                GROUP BY symbol
            ) x
            ON s.symbol = x.symbol AND s.asof_date = x.max_asof
            WHERE s.is_active = 1
              AND s.frequency = ?
              AND s.side_mode = ?
            ORDER BY s.symbol
        """
        params = (
            self.config.trade_config.frequency,
            self.config.trade_config.side_mode,
            session_date.isoformat(),
            *symbols,
            self.config.trade_config.frequency,
            self.config.trade_config.side_mode,
        )
        selected = self.db.query_df(query, params)
        if selected.empty:
            return f"default={default_sid}"

        selected_symbols = set(selected["symbol"].astype(str).tolist())
        missing = [s for s in symbols if s not in selected_symbols]
        unique_variants = sorted(selected["strategy_id"].astype(str).unique().tolist())

        if len(unique_variants) == 1 and not missing:
            return f"{unique_variants[0]} ({len(selected_symbols)} syms)"

        pairs = [f"{row.symbol}:{row.strategy_id}" for row in selected.itertuples(index=False)]
        text = " | ".join(pairs[:3])
        extra = max(0, len(pairs) - 3)
        if extra:
            text += f" | ...(+{extra})"
        if missing:
            text += f" | default->{','.join(missing[:3])}"
            if len(missing) > 3:
                text += f"(+{len(missing) - 3})"
        return text
