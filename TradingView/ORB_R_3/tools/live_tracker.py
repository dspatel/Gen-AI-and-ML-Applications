"""Live market-session tracker.

This is the "production shape" of the project: during a live session we continuously
fetch the newest intraday bars, detect breakouts vs multi-horizon reference ranges,
and emit alerts (console + optional Discord) while persisting artifacts (CSV + SQLite).

The goal of this first iteration is stability + correct behavior, not speed.

Usage (examples):
  # Run once (no loop), print events
  python -m tools.live_tracker --once --print-events

  # Loop, poll every 60s, send Discord
  python -m tools.live_tracker --poll-seconds 60 --send

Notes:
  - We use the config file for symbols, horizons, intervals, OR window, and breakouts.
  - We only track the *current* session day.
  - If started outside market hours, we exit with a message (for now).
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from orb_ref.data_fetch import FetchSpec
from orb_ref.data_provider import YFinanceProvider
from orb_ref.horizons import build_horizon_results
from orb_ref.lookback_behavior import aggregate_behavior, compute_day_behavior
from orb_ref.notifier import DiscordNotifier
from orb_ref.or_resolution import resolve_or_rows
from orb_ref.sessions import TradingSessions
from orb_ref.universe import load_symbols

from orb_ref.storage.store_factory import make_store


def _now_tz(tz: str) -> datetime:
    return datetime.now(tz=ZoneInfo(tz))


def main() -> None:
    p = argparse.ArgumentParser(description="Live ORB reference-range tracker")
    # Prefer a user-editable config/config.yml; keep config.example.yml as a template/fallback.
    p.add_argument("--config", default="config/config.yml")
    p.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    # If not provided, will use config `run.poll_seconds` (default 60).
    p.add_argument("--poll-seconds", type=int, default=None, help="Polling interval in seconds")
    # If not provided, will use config `run.status_every` (default 60).
    p.add_argument(
        "--status-every",
        type=int,
        default=None,
        help="Print a lightweight status line at most once every N seconds",
    )
    p.add_argument("--print-events", action="store_true", help="Print detected events")
    p.add_argument("--send", action="store_true", help="Send alerts via configured notifier")
    args = p.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))

    # Runtime defaults (can be provided via config under `run:`)
    run_cfg = cfg.get("run", {}) if isinstance(cfg, dict) else {}
    poll_seconds = int(args.poll_seconds if args.poll_seconds is not None else run_cfg.get("poll_seconds", 60))
    status_every = int(args.status_every if args.status_every is not None else run_cfg.get("status_every", 60))

    tz = cfg.get("market", {}).get("timezone", "America/Chicago")
    exchange = cfg.get("market", {}).get("exchange", "XNYS")
    interval = cfg.get("data", {}).get("interval", "1m")
    orb_minutes = int(cfg.get("orb", {}).get("orb_minutes", 30))

    horizons = cfg.get("reference", {}).get("horizons", [3, 5, 9])
    min_sessions_required = int(cfg.get("reference", {}).get("min_sessions_required", min(horizons)))

    breakout_cfg = cfg.get("breakouts", {}) or {}
    close_required = bool(breakout_cfg.get("close_required", True))
    confirm_closes = int(breakout_cfg.get("confirm_closes", 1))
    inside_reset_pct = float(breakout_cfg.get("inside_reset_pct", 0.10))
    min_bars_between_alerts = int(breakout_cfg.get("min_bars_between_alerts", 0))

    symbols = load_symbols(cfg)

    store = None
    storage_cfg = cfg.get("storage", {}) or {}
    if storage_cfg.get("enabled", False):
        store = make_store(storage_cfg)

    notifier = None
    notif_cfg = cfg.get("notifications", {}) or {}
    if notif_cfg.get("enabled", False) and args.send:
        notifier = DiscordNotifier.from_config(notif_cfg)

    ts = TradingSessions(exchange=exchange, tz=tz)
    now = _now_tz(tz)
    session_date = now.date()
    if not ts.cal.is_session(session_date.isoformat()):
        print(f"{session_date} is not a trading session for {exchange}. Exiting.")
        return

    bounds = ts.get_session_bounds(session_date)

    # Banner so it's obvious the process is alive (the loop can be quiet if no signals fire).
    print(
        "Live tracker started | "
        f"date={session_date} session={bounds.open_dt:%H:%M}-{bounds.close_dt:%H:%M} {tz} "
        f"interval={interval} ORB={orb_minutes}m horizons={horizons} symbols={len(symbols)} "
        f"poll={poll_seconds}s status_every={status_every}s"
    )

    # If launched pre-market, wait until the session open.
    if now < bounds.open_dt:
        wait_s = max(1, int((bounds.open_dt - now).total_seconds()))
        print(f"Pre-market: now={now:%H:%M:%S} {tz}. Waiting {wait_s}s until open...")
        time.sleep(min(wait_s, poll_seconds))

    # For the first live version, we run a very simple loop:
    #  - fetch full session bars up to *now*
    #  - compute lookback OR rows (DB-first)
    #  - compute horizon reference ranges + behavior summaries
    #  - detect breakouts on the latest candle and emit alerts
    #
    # The per-symbol/horizon state is kept in-memory for the process lifetime.

    provider = YFinanceProvider()

    # state: {symbol: detector_state}
    detector_state: dict[str, dict] = {}

    # status/heartbeat helpers (this tool can be quiet when no signals fire)
    last_bar_by_sym: dict[str, str] = {}
    # Simple counters for quick observability + end-of-day summary
    total_events = 0
    events_by_sym: dict[str, int] = {s: 0 for s in symbols}
    events_by_horizon: dict[int, int] = {h: 0 for h in horizons}

    # We track the last time we printed a status line so users can tell the tracker is alive.
    # Use timezone-aware datetimes (same tz as `now`).
    last_status_ts: datetime | None = None

    while True:
        cycle_start = datetime.now()
        cycle_events_count = 0
        now = _now_tz(tz)

        # Exit once market is closed.
        bounds = ts.get_session_bounds(session_date)
        if now < bounds.open_dt:
            # Before the session opens, we just wait. We still print a heartbeat periodically.
            time_to_open = (bounds.open_dt - now).total_seconds()
            sleep_s = max(1, int(min(poll_seconds, time_to_open)))
            if status_every and (last_status_ts is None or (now - last_status_ts).total_seconds() >= float(status_every)):
                last_status_ts = now
                print(f"[{now.strftime('%H:%M:%S')} {tz}] waiting for market open at {bounds.open_dt.strftime('%H:%M:%S')} | sleep {sleep_s}s")
            time.sleep(sleep_s)
            continue
        if now > bounds.close_dt:
            print(f"Market closed for {session_date}. Exiting live tracker.")
            break

        for sym in symbols:
            spec = FetchSpec(symbol=sym, asof_date=session_date, interval=interval, tz=tz, exchange=exchange)

            # Determine the historical sessions we need.
            sessions = ts.get_prev_sessions(session_date, max(horizons))
            # NOTE: live tracker currently does NOT include today's OR in references.
            # We can add that in a later phase.

            # DB-first OR resolution
            or_rows = []
            if store is not None:
                or_rows = resolve_or_rows(
                    sessions=sessions,
                    spec=spec,
                    ts=ts,
                    orb_minutes=orb_minutes,
                    store=store,
                    provider=provider,
                )
            else:
                # Fallback: compute ORs from fetched bars (no persistence)
                from orb_ref.ranges_or import compute_daily_or
                from orb_ref.data_fetch import fetch_session_bars

                for d in sessions:
                    df_d = fetch_session_bars(spec, d, provider=provider)
                    if df_d is None or df_d.empty:
                        continue
                    or_start, or_end = ts.get_or_window_bounds(d, orb_minutes)
                    r = compute_daily_or(df_d, or_start, or_end)
                    if r:
                        or_rows.append(r)

            # Need enough sessions to compute smallest horizon
            if len(or_rows) < min_sessions_required:
                continue

            # For behavior we compute intensity vs that day's OR *price* levels.
            # (Important: compute_day_behavior expects numeric OR high/low, not OR window timestamps.)
            beh_rows = []
            from orb_ref.data_fetch import fetch_session_bars

            or_by_date = {r.get("session_date"): r for r in (or_rows or []) if r and r.get("session_date")}

            for d in sessions:
                df_d = fetch_session_bars(spec, d, provider=provider)
                if df_d is None or df_d.empty:
                    continue

                r = or_by_date.get(d.isoformat())
                if not r:
                    continue

                beh = compute_day_behavior(df_d, float(r["or_high"]), float(r["or_low"]))
                if beh:
                    beh_rows.append(beh)
            agg_beh = aggregate_behavior(beh_rows)

            # Build horizon results (reference + behavior slice)
            horizon_res = build_horizon_results(
                or_rows,
                beh_rows,
                horizons=horizons,
                min_sessions_required=min_sessions_required,
            )

            # Fetch today's bars up to now (we use full-day fetch and slice to now; next phase can optimize)
            df_today = fetch_session_bars(spec, session_date, provider=provider)
            if df_today is None or df_today.empty:
                continue
            df_today = df_today[df_today.index <= bounds.close_dt]
            df_today = df_today[df_today.index <= now]

            # record last bar time for status/heartbeat
            if not df_today.empty:
                try:
                    last_ts = df_today.index[-1]
                    if hasattr(last_ts, "astimezone"):
                        last_ts = last_ts.astimezone(tz)
                    last_bar_by_sym[sym] = last_ts.strftime("%H:%M")
                except Exception:
                    pass
            if df_today.empty:
                continue

            # Evaluate breakouts for each horizon on latest candle
            from orb_ref.breakouts import detect_breakouts_close_only_stepwise
            from orb_ref.notifications import render_message

            sym_state = detector_state.get(sym) or {}
            new_events_all = []
            for h, res in horizon_res.items():
                # `horizon_res` values are `HorizonResult` dataclass instances (preferred),
                # but we keep backwards compatibility if a plain dict is returned.
                if hasattr(res, "ref"):
                    ref = getattr(res, "ref") or {}
                elif isinstance(res, dict):
                    ref = res.get("ref") or {}
                else:
                    ref = {}
                if not ref:
                    continue
                # init per-horizon state
                key = str(h)
                h_state = sym_state.get(key)
                # NOTE: detect_breakouts_close_only_stepwise expects (df, ref_low, ref_high, params=None, state=None)
                new_events, h_state = detect_breakouts_close_only_stepwise(
                    df_today,
                    float(ref["ref_low"]),
                    float(ref["ref_high"]),
                    {
                        "confirm_closes": confirm_closes,
                        "inside_reset_pct": inside_reset_pct,
                        "min_bars_between_alerts": min_bars_between_alerts,
                        "close_required": close_required,
                    },
                    state=h_state,
                )
                sym_state[key] = h_state
                for e in new_events:
                    e["horizon"] = int(h)
                    e["symbol"] = sym
                    # attach context blocks
                    e.update(ref)
                    e.update(agg_beh)
                new_events_all.extend(new_events)

            detector_state[sym] = sym_state

            # Emit events
            if new_events_all:
                cycle_events_count += len(new_events_all)
                total_events += len(new_events_all)
                events_by_sym[sym] = events_by_sym.get(sym, 0) + len(new_events_all)
                for e in new_events_all:
                    try:
                        h = int(e.get("horizon_days"))
                        events_by_horizon[h] = events_by_horizon.get(h, 0) + 1
                    except Exception:
                        pass
                # sort by timestamp
                new_events_all.sort(key=lambda x: x.get("timestamp"))
                templates = cfg.get("templates", {}) or {}
                for e in new_events_all:
                    payload = {
                        "symbol": sym,
                        "asof_date": session_date.isoformat(),
                        "timestamp": e.get("timestamp"),
                        "direction": e.get("direction"),
                        **e,
                    }
                    msg = render_message(templates, "default", payload)
                    if args.print_events:
                        print(msg)
                        print("-" * 60)
                    if notifier is not None:
                        notifier.send(payload.get("title", f"{sym} breakout"), msg)

        # Periodic heartbeat/status so you can tell the tracker is running even if no alerts fire.
        if status_every and float(status_every) > 0:
            now3 = _now_tz(tz)
            if last_status_ts is None or (now3 - last_status_ts).total_seconds() >= float(status_every):
                last_status_ts = now3
                lastbars = " ".join([f"{k}:{v}" for k, v in sorted(last_bar_by_sym.items())]) or "n/a"
                print(
                    f"[{now3.strftime('%H:%M:%S')} {tz}] heartbeat | lastbars {lastbars} | events +{cycle_events_count} (total {total_events}) | next poll ~{poll_seconds}s"
                )

        if args.once:
            return
        # simple poll sleep
        elapsed = (datetime.now() - cycle_start).total_seconds()
        sleep_s = max(1, int(poll_seconds - elapsed))
        time.sleep(sleep_s)

    # --- loop ended (e.g., market closed) ---
    send_summary = bool(notif_cfg.get("send_summary", True))
    if notifier is not None and send_summary:
        by_sym_txt = " | ".join([f"{s}:{events_by_sym.get(s,0)}" for s in symbols]) or "n/a"
        by_h_txt = " | ".join([f"{h}D:{events_by_horizon.get(int(h),0)}" for h in horizons]) or "n/a"
        summary_msg = (
            f"\N{BAR CHART} End of day summary ({session_date.date()} {tz})\n"
            f"Total events: {total_events}\n"
            f"By symbol: {by_sym_txt}\n"
            f"By horizon: {by_h_txt}"
        )
        notifier.send("EOD Summary", summary_msg)


if __name__ == "__main__":
    main()
