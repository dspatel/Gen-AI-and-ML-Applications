from __future__ import annotations
import os
import time
import pandas as pd
from zoneinfo import ZoneInfo
from typing import Dict

from orb_monitor.config import Config
from orb_monitor.data import fetch_bars, valid_test_date
from orb_monitor.strategy import (
    SymbolState,
    session_bounds,
    filter_session,
    build_opening_range,
    scan_catchup_events,
    process_latest_two_bars_live,
    is_within_range,
)
from orb_monitor.loggers import log_path, append_event
from orb_monitor.notify import (
    notify_event_discord,
    notify_market_close_summary_discord,
    notify_or_created_discord,
)
from orb_monitor.state_store import load_symbol_state, apply_loaded_state, save_symbol_state


# -----------------------------------------------------------------------------
# In-process catchup notification counters (per symbol)
# Used to limit spam when starting mid-session (resets each run)
# -----------------------------------------------------------------------------
CATCHUP_NOTIFY_COUNTS: dict[str, int] = {}


def clear_screen(cfg: Config) -> None:
    """Clear terminal for a simple dashboard feel."""
    if cfg.clear_console_each_tick:
        os.system("cls" if os.name == "nt" else "clear")


def next_bar_close(now: pd.Timestamp, minutes: int) -> pd.Timestamp:
    """
    Compute next bar close aligned to the candle size.
    Example: minutes=5 => 09:35, 09:40, 09:45 ...
    """
    floored = now.replace(second=0, microsecond=0)
    m = floored.minute
    next_m = ((m // minutes) + 1) * minutes
    if next_m < 60:
        return floored.replace(minute=next_m)
    return (floored + pd.Timedelta(hours=1)).replace(minute=0)


def notify_event(cfg: Config, ev) -> None:
    """Dispatch notifications for a single event (Discord now; email can be added later)."""
    if not cfg.enable_notifications:
        return
    # Catchup notifications can be noisy when starting mid-session.
    # Keep them OFF by default; enable via cfg.send_catchup_notifications.
    if getattr(ev, "is_catchup", False):
        if not cfg.send_catchup_notifications:
            return
        # Limit catchup notifications per symbol to avoid spam on startup
        sym = getattr(ev, "symbol", "UNKNOWN")
        CATCHUP_NOTIFY_COUNTS[sym] = CATCHUP_NOTIFY_COUNTS.get(sym, 0) + 1
        if CATCHUP_NOTIFY_COUNTS[sym] > cfg.catchup_notify_limit_per_symbol:
            return
    if cfg.enable_discord:
        notify_event_discord(cfg, ev)


def notify_or_created(cfg: Config, symbol: str, session_date: str, or_high: float, or_low: float,
                      or_window_end: pd.Timestamp, is_catchup: bool) -> None:
    """Notify once per symbol when the Opening Range is established."""
    if not cfg.enable_notifications:
        return
    if not cfg.notify_on_or_creation:
        return
    if cfg.enable_discord:
        notify_or_created_discord(cfg, symbol, session_date, or_high, or_low, or_window_end, is_catchup)


def render_dashboard(cfg: Config, states: Dict[str, SymbolState], now_local: pd.Timestamp) -> str:
    """Render a compact terminal dashboard."""
    lines = []
    lines.append(
        f"ORB LIVE | now={str(now_local)[:19]} {cfg.tz} | candle={cfg.candle_minutes}m | TEST={cfg.test_mode}"
    )
    lines.append("-" * 120)
    lines.append(
        f"{'SYM':<6} {'OR_READY':<8} {'ARMED':<6} {'ORH':>10} {'ORL':>10} {'LAST_CONFIRM':>19} {'EVENTS':>6}"
    )
    lines.append("-" * 120)

    for sym, st in states.items():
        orh = f"{st.or_high:.2f}" if st.or_high is not None else "---"
        orl = f"{st.or_low:.2f}" if st.or_low is not None else "---"
        last = str(st.last_confirm_dt_processed)[:19] if st.last_confirm_dt_processed is not None else "---"
        lines.append(
            f"{sym:<6} {str(st.or_ready):<8} {str(st.armed):<6} {orh:>10} {orl:>10} {last:>19} {len(st.events):>6}"
        )

        if st.events:
            recent = st.events[-cfg.show_last_n_events:]
            for e in recent:
                arrow = "⬆️" if "UP" in e.direction else "⬇️"
                tag = "catchup" if e.is_catchup else "live"
                lines.append(
                    f"      {arrow} [{tag}] confirm={str(e.confirm_dt)[:19]}  C={e.confirm_close:.2f}  V={e.confirm_volume}"
                )

    return "\n".join(lines)


def main() -> None:
    cfg = Config()

    # Optional: load symbols from symbols.txt (one ticker per line)
    # IMPORTANT: this must be AFTER cfg = Config() (fixes NameError)
    from pathlib import Path
    sym_file = Path("symbols.txt")
    if sym_file.exists():
        syms = [s.strip().upper() for s in sym_file.read_text(encoding="utf-8").splitlines() if s.strip()]
        if syms:
            cfg.symbols = syms

    # Validate test_date (prevents mis-paste like a URL)
    if cfg.test_mode and cfg.test_date and not valid_test_date(cfg.test_date):
        print(f"WARNING: test_date='{cfg.test_date}' invalid. Use YYYY-MM-DD or empty. Falling back to AUTO.")
        cfg.test_date = ""

    tz = ZoneInfo(cfg.tz)

    # Determine session day
    if cfg.test_mode and cfg.test_date:
        session_day = pd.Timestamp(cfg.test_date).tz_localize(tz).normalize()
    else:
        # In LIVE mode, if you start before the open, Yahoo often only has yesterday's bars.
        # So we anchor the session day to 'today' in the configured timezone.
        session_day = pd.Timestamp.now(tz=tz).normalize()

    session_start, session_end = session_bounds(cfg, session_day)
    or_window_end = session_start + pd.Timedelta(minutes=cfg.orb_minutes)
    session_date_str = str(session_day.date())
    lp = log_path(cfg, session_date_str)

    states: Dict[str, SymbolState] = {
        sym: SymbolState(sym, session_date_str, session_start, session_end)
        for sym in cfg.symbols
    }

    # =============================
    # RESTORE PERSISTED STATE (restart-safe)
    # =============================
    if cfg.persist_state:
        db_path = cfg.state_db_path
        for sym, st in states.items():
            row = load_symbol_state(db_path, session_date_str, sym)
            if row:
                apply_loaded_state(st, row)

    print(f"Symbols: {', '.join(cfg.symbols)}")
    print(f"Session: {session_start} → {session_end} ({cfg.tz})")
    print(f"Logs: {lp}")
    print("Starting…\n")

    # =============================
    # CATCHUP PHASE
    # =============================
    # In live mode: catch up from market open → script start time
    # In test mode: catch up for the entire session and then exit (no infinite loop)
    startup_now = pd.Timestamp.now(tz=tz)
    catchup_until = session_end if cfg.test_mode else startup_now

    for sym in cfg.symbols:
        try:
            bars = fetch_bars(sym, cfg)
        except Exception as e:
            print(f"[{sym}] fetch error: {e}")
            continue

        sess = filter_session(bars, session_start, session_end)
        if sess.empty:
            print(f"[{sym}] no session data.")
            continue

        st = states[sym]

        if len(sess) >= cfg.orb_bars:
            try:
                or_high, or_low = build_opening_range(cfg, sess)
            except Exception as e:
                print(f"[{sym}] OR build error: {e}")
                continue

            st.or_high, st.or_low, st.or_ready = or_high, or_low, True

            # Notify OR created (one-time, persisted across restarts)
            if not st.or_notified and cfg.notify_on_or_creation:
                notify_or_created(
                    cfg,
                    sym,
                    session_date_str,
                    or_high,
                    or_low,
                    or_window_end,
                    is_catchup=(startup_now > or_window_end),
                )
                st.or_notified = True

            catchups = scan_catchup_events(
                cfg=cfg,
                symbol=sym,
                session_date=session_date_str,
                session_df=sess,
                or_high=or_high,
                or_low=or_low,
                notify_until_dt=catchup_until,
                min_confirm_dt_exclusive=st.last_confirm_dt_processed,
            )

            for ev in catchups:
                st.events.append(ev)
                append_event(cfg, lp, ev)
                notify_event(cfg, ev)

            # Advance watermark to the latest CLOSED bar we actually have up to catchup_until
            sess_upto = sess[sess["time_local"] <= catchup_until]
            if not sess_upto.empty:
                st.last_confirm_dt_processed = sess_upto.iloc[-1]["time_local"]

            # Reconcile armed/disarmed after catchup so LIVE doesn't re-alert incorrectly
            if cfg.rearm_after_reentry and not sess_upto.empty:
                last_close = float(sess_upto.iloc[-1]["close"])
                if catchups:
                    # If we saw a breakout in catchup, we should be disarmed unless price closed back inside OR.
                    st.armed = is_within_range(cfg, last_close, st.or_high, st.or_low)
                else:
                    # If state says we're disarmed, check whether we've re-entered.
                    if not st.armed:
                        st.armed = is_within_range(cfg, last_close, st.or_high, st.or_low)

            # Persist updated state
            if cfg.persist_state:
                save_symbol_state(cfg.state_db_path, st)

    # If testing, send summary and exit now
    if cfg.test_mode:
        clear_screen(cfg)
        print(render_dashboard(cfg, states, pd.Timestamp.now(tz=tz)))
        if cfg.enable_discord and cfg.enable_notifications:
            notify_market_close_summary_discord(cfg, states, session_date_str)
        print("\nTEST mode complete. Exiting.")
        return

    # =============================
    # LIVE LOOP (market hours only)
    # =============================
    while True:
        now_local = pd.Timestamp.now(tz=tz)

        if now_local >= session_end:
            clear_screen(cfg)
            print(render_dashboard(cfg, states, now_local))
            if cfg.enable_discord and cfg.enable_notifications:
                notify_market_close_summary_discord(cfg, states, session_date_str)
            print("\nMarket closed. Exiting.")
            break

        if now_local < session_start:
            clear_screen(cfg)
            print(render_dashboard(cfg, states, now_local))
            time.sleep(cfg.poll_fallback_seconds)
            continue

        # Sleep until next bar close + grace
        try:
            nxt = next_bar_close(now_local, cfg.candle_minutes) + pd.Timedelta(seconds=cfg.grace_seconds)
            sleep_s = max(1.0, (nxt - pd.Timestamp.now(tz=tz)).total_seconds())
            time.sleep(sleep_s)
        except Exception:
            time.sleep(cfg.poll_fallback_seconds)

        for sym in cfg.symbols:
            try:
                bars = fetch_bars(sym, cfg)
            except Exception as e:
                print(f"[{sym}] fetch error: {e}")
                continue

            sess = filter_session(bars, session_start, session_end)
            if sess.empty:
                continue

            st = states[sym]

            # If OR becomes ready during LIVE (i.e., we started before the OR window completes),
            # send the OR-created notification as soon as the OR is computed.
            if st.or_ready and (not st.or_notified) and cfg.notify_on_or_creation:
                notify_or_created(
                    cfg,
                    sym,
                    session_date_str,
                    float(st.or_high),
                    float(st.or_low),
                    or_window_end,
                    is_catchup=False,
                )
                st.or_notified = True

            ev = process_latest_two_bars_live(cfg, st, sess)
            if ev is not None:
                st.events.append(ev)
                append_event(cfg, lp, ev)
                notify_event(cfg, ev)

            if cfg.persist_state:
                save_symbol_state(cfg.state_db_path, st)

        if cfg.show_dashboard:
            clear_screen(cfg)
            print(render_dashboard(cfg, states, pd.Timestamp.now(tz=tz)))


if __name__ == "__main__":
    main()