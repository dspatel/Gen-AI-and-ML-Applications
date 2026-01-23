import os
import pandas as pd

from config import CFG
from utils.io_utils import ensure_dirs, today_ymd, read_df, save_df, save_df_guarded
from utils.sqlite_store import (
    connect_db,
    init_db,
    init_state_tables,
    init_alerts_log,
    read_daily_bars,
    get_symbol_state,
    upsert_symbol_state,
    set_armed,
    set_alert_info,
    insert_alert_log,
    read_alerts_for_date,
)
from utils.indicators import find_latest_range_cross
from utils.discord_notify import send_discord_message, format_eod_summary, format_alerts_table


def compute_anchored_window_before_cross(df: pd.DataFrame, cross_date: str, window_days: int):
    """
    Frozen window = last `window_days` trading bars strictly BEFORE cross_date (cross date excluded).
    Returns (window_high, window_low) or None if insufficient history.
    """
    cross_dt = pd.to_datetime(cross_date).date()
    pre = df[df["Date"].dt.date < cross_dt].copy()
    if len(pre) < window_days:
        return None
    lastn = pre.tail(window_days)
    return float(lastn["High"].max()), float(lastn["Low"].min())


def is_reentry(close: float, window_low: float, window_high: float) -> bool:
    if CFG.REENTRY_MODE == "inclusive":
        return (close >= window_low) and (close <= window_high)
    return (close > window_low) and (close < window_high)


def main():
    ensure_dirs(CFG.SYMBOLS_DIR, CFG.OUTPUT_DIR, os.path.dirname(CFG.DB_PATH))

    run_date = today_ymd()

    # Pick latest symbols_*.csv as universe
    symbol_files = sorted([f for f in os.listdir(CFG.SYMBOLS_DIR) if f.startswith("symbols_") and f.endswith(".csv")])
    if not symbol_files:
        raise FileNotFoundError(f"No symbols_*.csv found in {CFG.SYMBOLS_DIR}")

    symbols_path = os.path.join(CFG.SYMBOLS_DIR, symbol_files[-1])
    symbols_df = read_df(symbols_path)
    symbols = symbols_df["Symbol"].astype(str).tolist()
    total_symbols = len(symbols)

    conn = connect_db(CFG.DB_PATH)
    init_db(conn, wal_mode=CFG.SQLITE_WAL_MODE)
    init_state_tables(conn)
    if getattr(CFG, "ENABLE_ALERTS_LEDGER", True):
        init_alerts_log(conn)

    all_scan_rows = []
    cross_universe_rows = []
    eligible_count = 0

    # Determine date used for evaluation (supports backtest-ish replay if you set CFG.BACKTEST_MODE + start/end)
    # Step 3 is EOD scanner: we evaluate as of the latest daily bar in DB for each symbol.
    for sym in symbols:
        limit_rows = getattr(CFG, "YF_READ_LIMIT_ROWS", CFG.SQLITE_CACHE_DAYS_PER_SYMBOL)
        df = read_daily_bars(conn, sym, limit_rows=limit_rows)
        if df is None or df.empty:
            continue

        df = df.sort_values("Date").reset_index(drop=True)

        # Latest crossover within last N trading days
        cross = find_latest_range_cross(df, ema_col="EMA20", lookback_days=CFG.CROSS_LOOKBACK_DAYS)
        if cross is None:
            continue

        eligible_count += 1
        latest_cross_date = cross["cross_date"]
        latest_cross_dir = cross["direction"]

        cross_universe_rows.append({
            "Symbol": sym,
            "LatestCrossDate": latest_cross_date,
            "LatestCrossDirection": latest_cross_dir,
        })

        # Today row (latest available)
        today = df.iloc[-1]
        today_date = today["Date"].date().isoformat()
        close = float(today["Close"])
        ema20 = float(today["EMA20"])
        ema20_h = float(today.get("EMA20_H", float("nan")))
        ema20_l = float(today.get("EMA20_L", float("nan")))

        # State from DB
        state = get_symbol_state(conn, sym)

        need_refresh = (
            state is None
            or state.get("last_cross_date") != latest_cross_date
            or state.get("window_high_7") is None
            or state.get("window_low_7") is None
            or state.get("window_high_21") is None
            or state.get("window_low_21") is None
        )

        if need_refresh:
            win7 = compute_anchored_window_before_cross(df, latest_cross_date, CFG.WINDOW_DAYS_SHORT)
            win21 = compute_anchored_window_before_cross(df, latest_cross_date, CFG.WINDOW_DAYS_LONG)
            if win7 is None or win21 is None:
                # Not enough history
                all_scan_rows.append({
                    "Symbol": sym,
                    "AsOfDate": today_date,
                    "Eligible": True,
                    "LatestCrossDate": latest_cross_date,
                    "LatestCrossDirection": latest_cross_dir,
                    "WindowReady": False,
                })
                continue

            window_high_7, window_low_7 = win7
            window_high_21, window_low_21 = win21

            # Upsert new cycle, armed=1
            upsert_symbol_state(
                conn,
                sym,
                latest_cross_date,
                latest_cross_dir,
                window_high_7,  # legacy mapping
                window_low_7,   # legacy mapping
                1,
                window_high_7=window_high_7,
                window_low_7=window_low_7,
                window_high_21=window_high_21,
                window_low_21=window_low_21,
            )
            state = get_symbol_state(conn, sym)

        # Pull frozen windows (prefer explicit cols; fallback to legacy)
        window_high_7 = float(state.get("window_high_7") or state.get("window_high"))
        window_low_7 = float(state.get("window_low_7") or state.get("window_low"))
        window_high_21 = float(state.get("window_high_21") or window_high_7)
        window_low_21 = float(state.get("window_low_21") or window_low_7)

        armed = int(state.get("armed", 1))

        # Rearm on re-entry
        reentry_today = False
        if CFG.REARM_ON_REENTRY and armed == 0 and is_reentry(close, window_low_7, window_high_7):
            reentry_today = True
            set_armed(conn, sym, True)
            armed = 1

        # Allowed-by-cross toggle
        cross_dt = pd.to_datetime(latest_cross_date).date()
        today_dt = pd.to_datetime(today_date).date()
        allowed_by_cross_toggle = (today_dt >= cross_dt) if CFG.ALLOW_ALERT_ON_CROSS_DATE else (today_dt > cross_dt)

        # Evaluate event
        long_candidate = bool(allowed_by_cross_toggle and armed == 1 and close > window_high_7 and close > ema20)
        short_candidate = bool(allowed_by_cross_toggle and armed == 1 and close < window_low_7 and close < ema20)

        signal = "LONG" if long_candidate else ("SHORT" if short_candidate else "")

        # Metrics
        rng7 = max(window_high_7 - window_low_7, 1e-9)
        rng21 = max(window_high_21 - window_low_21, 1e-9)

        break_dist_7 = (close - window_high_7) if long_candidate else ((window_low_7 - close) if short_candidate else 0.0)
        break_dist_21 = (close - window_high_21) if long_candidate else ((window_low_21 - close) if short_candidate else 0.0)
        break_pct_7 = break_dist_7 / rng7
        break_pct_21 = break_dist_21 / rng21
        ema_dist = (close - ema20) if long_candidate else ((ema20 - close) if short_candidate else (close - ema20))

        scan_row = {
            "Symbol": sym,
            "AsOfDate": today_date,
            "Eligible": True,
            "LatestCrossDate": latest_cross_date,
            "LatestCrossDirection": latest_cross_dir,
            "WindowReady": True,
            "EMA20": ema20,
            "EMA20_H": ema20_h,
            "EMA20_L": ema20_l,
            "WindowHigh_7D_preCross": window_high_7,
            "WindowLow_7D_preCross": window_low_7,
            "WindowHigh_21D_preCross": window_high_21,
            "WindowLow_21D_preCross": window_low_21,
            "ArmedBeforeEval": bool(armed),
            "ReentryToday": bool(reentry_today),
            "AllowedByCrossToggle": bool(allowed_by_cross_toggle),
            "LongCandidate": bool(long_candidate),
            "ShortCandidate": bool(short_candidate),
            "Signal": signal,
            "BreakPctOfRange_7D": break_pct_7,
            "BreakPctOfRange_21D": break_pct_21,
            "EmaDistance": ema_dist,
        }
        all_scan_rows.append(scan_row)

        if signal:
            # Disarm
            set_armed(conn, sym, False)
            set_alert_info(conn, sym, today_date, signal)

            alert = {
                "Symbol": sym,
                "EventDate": today_date,
                "EventTime": None,
                "Signal": signal,
                "TodayClose": close,
                "EMA20": ema20,
                "EMA20_H": ema20_h,
                "EMA20_L": ema20_l,
                "WindowHigh_7D_preCross": window_high_7,
                "WindowLow_7D_preCross": window_low_7,
                "WindowHigh_21D_preCross": window_high_21,
                "WindowLow_21D_preCross": window_low_21,
                "BreakPctOfRange_7D": break_pct_7,
                "BreakPctOfRange_21D": break_pct_21,
                "EmaDistance": ema_dist,
                "LatestCrossDate": latest_cross_date,
                "LatestCrossDirection": latest_cross_dir,
            }

            if getattr(CFG, "ENABLE_ALERTS_LEDGER", True):
                insert_alert_log(conn, alert, source="EOD")

    # Save scan_all
    scan_all_df = pd.DataFrame(all_scan_rows).sort_values(["AsOfDate","Symbol"], ascending=[False, True])
    scan_all_path = os.path.join(CFG.OUTPUT_DIR, f"scan_all_{run_date}.csv")
    save_df(scan_all_df, scan_all_path)

    # Cross universe file toggle
    if getattr(CFG, "SAVE_EMA20_CROSS_SYMBOLS", True):
        cross_df = pd.DataFrame(cross_universe_rows).sort_values("Symbol")
        cross_path = os.path.join(CFG.SYMBOLS_DIR, f"ema20_cross_{run_date}.csv")
        save_df(cross_df, cross_path)

    # Build alerts file from ledger if enabled (this prevents live runs from wiping)
    if getattr(CFG, "ENABLE_ALERTS_LEDGER", True):
        alerts_df = read_alerts_for_date(conn, run_date)
    else:
        # Fallback: alerts from scan_all rows where Signal != ""
        alerts_df = scan_all_df[scan_all_df["Signal"].astype(str).str.len() > 0].copy()
        if not alerts_df.empty:
            alerts_df = alerts_df.rename(columns={"AsOfDate":"EventDate"})

    alerts_path = os.path.join(CFG.OUTPUT_DIR, f"scan_alerts_{run_date}.csv")
    save_df_guarded(alerts_df, alerts_path, preserve_if_empty=getattr(CFG, "PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY", True))

    # Console summary
    longs = int((alerts_df["Signal"] == "LONG").sum()) if not alerts_df.empty else 0
    shorts = int((alerts_df["Signal"] == "SHORT").sum()) if not alerts_df.empty else 0
    alerts_count = int(len(alerts_df))

    print("\n" + "=" * 120)
    print(f"EOD SCAN COMPLETE | {run_date}")
    print("=" * 120)
    print(f"Universe symbols: {total_symbols} (from {os.path.basename(symbols_path)})")
    print(f"Cross-eligible: {eligible_count}")
    print(f"Alerts (ledger): {alerts_count} (LONG {longs} | SHORT {shorts})")
    print(f"Saved scan_all:    {scan_all_path}")
    print(f"Saved alerts:      {alerts_path}")
    if getattr(CFG, "SAVE_EMA20_CROSS_SYMBOLS", True):
        print(f"Saved cross file:  {os.path.join(CFG.SYMBOLS_DIR, f'ema20_cross_{run_date}.csv')}")
    print("=" * 120 + "\n")

    # Discord EOD notifications
    if getattr(CFG, "DISCORD_ENABLED", False) and getattr(CFG, "DISCORD_WEBHOOK_URL", ""):
        env = getattr(CFG, "DISCORD_ENV", "TEST")
        if getattr(CFG, "DISCORD_SEND_EOD_SUMMARY", True):
            msg = format_eod_summary(run_date, total_symbols, eligible_count, alerts_count, longs, shorts, env=env)
            send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)
        if getattr(CFG, "DISCORD_SEND_EOD_ALERTS_TABLE", True):
            # Convert to list of dicts for formatting
            alerts_list = alerts_df.to_dict(orient="records") if not alerts_df.empty else []
            table = format_alerts_table(alerts_list, max_rows=getattr(CFG, "DISCORD_MAX_ALERTS", 10))
            send_discord_message(CFG.DISCORD_WEBHOOK_URL, table)

    conn.close()


if __name__ == "__main__":
    main()
