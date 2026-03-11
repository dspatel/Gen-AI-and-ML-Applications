from __future__ import annotations

import pandas as pd
import sys

from .config_loader import load_config
from .db import connect, init_db
from .symbols import load_symbols
from .prepare_asof import ensure_asof_ready
from .time_utils import combine_cst_date_time
from .notifier import load_templates, send_discord
from .breakouts import ensure_breakout_tables, load_rr_rows, HorizonState
from .breakout_engine import load_day_candles, evaluate_bar_close_only


def _safe_print(text: str) -> None:
    enc = (sys.stdout.encoding or "utf-8")
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def run(config_path: str = "orb_r6_config.yaml") -> None:
    cfg = load_config(config_path)
    conn = connect(cfg.db_path)
    init_db(conn)
    ensure_breakout_tables(conn)

    asof_date = cfg.asof_date_cst
    if not asof_date:
        raise ValueError("config.yaml asof_date_cst is required for replay")

    # DB-first: ensure candles/OR/RR exist for the as-of session date
    ensure_asof_ready(conn, cfg, asof_date)

    replay_cfg = getattr(cfg, "replay", {}) or {}
    tag_replay = bool(replay_cfg.get("tag_replay_alerts", True))
    tag = "[REPLAY] " if tag_replay else ""

    discord_cfg = getattr(cfg, "discord", {}) or {}
    discord_enabled = bool(discord_cfg.get("enabled", False))
    webhook = discord_cfg.get("webhook_url", "")
    templates_path = discord_cfg.get("templates_path", "./templates/discord_alerts.yaml")
    templates = load_templates(templates_path)

    symbols = load_symbols(cfg.symbols)
    interval = cfg.market_data.interval
    horizons = sorted(cfg.market_data.lookback_days)
    or_minutes = int(cfg.market_data.opening_range_minutes)

    print("-" * 60)
    print(f"REPLAY: {asof_date} interval={interval} symbols={symbols} horizons={horizons}")
    print("-" * 60)

    session_open_ts_cst = combine_cst_date_time(asof_date, cfg.session.start)
    or_end_ts_cst = session_open_ts_cst + pd.Timedelta(minutes=or_minutes)

    for sym in symbols:
        rr_pre = load_rr_rows(conn, sym, asof_date, or_minutes, interval, include_today_or=0)
        rr_post = load_rr_rows(conn, sym, asof_date, or_minutes, interval, include_today_or=1)

        rr_seed = rr_pre if rr_pre else rr_post
        if not rr_seed:
            print(f"[WARN] {sym}: No complete RR rows for {asof_date}.")
            continue

        df = load_day_candles(conn, sym, interval, asof_date)
        if df.empty:
            print(f"[WARN] {sym}: No candles for {asof_date} at {interval}.")
            continue

        state_by_phase = {0: {}, 1: {}}

        for _, row in df.iterrows():
            phase_for_row = 0 if pd.to_datetime(row["close_ts_cst"]) < pd.Timestamp(or_end_ts_cst) else 1
            res = evaluate_bar_close_only(
                conn,
                templates=templates,
                discord_enabled=discord_enabled,
                webhook=webhook,
                tag=tag,
                mode="REPLAY",
                symbol=sym,
                asof_date_cst=asof_date,
                interval=interval,
                or_minutes=or_minutes,
                horizons=horizons,
                rr_pre=rr_pre,
                rr_post=rr_post,
                rr_seed=rr_seed,
                state_by_h=state_by_phase[phase_for_row],
                row=row,
                or_end_ts_cst=or_end_ts_cst,
            )

            if (not discord_enabled) and res.content:
                _safe_print(res.content)

    conn.close()
    print("-" * 60)
    print("[REPLAY DONE]")
    print("-" * 60)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
