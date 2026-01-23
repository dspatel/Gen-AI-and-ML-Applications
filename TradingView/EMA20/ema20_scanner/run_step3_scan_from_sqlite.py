import os
import pandas as pd

from config import CFG
from utils.io_utils import ensure_dirs, today_ymd, read_df, save_df
from utils.sqlite_store import (
    connect_db,
    init_db,
    init_state_tables,
    read_daily_bars,
    get_symbol_state,
    upsert_symbol_state,
    set_armed,
    set_alert_info,
)
from utils.indicators import find_latest_range_cross


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
    # strict (recommended)
    return (close > window_low) and (close < window_high)


def _fmt(x, digits=2):
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return str(x)


def print_dashboard(run_date: str, total_symbols: int, eligible_symbols: int, alerts_df: pd.DataFrame, max_rows: int = 50):
    print("\n" + "=" * 140)
    print(f"EOD SCANNER DASHBOARD | Run Date: {run_date}")
    print("=" * 140)
    print(f"Universe symbols (Step 1): {total_symbols}")
    print(f"Eligible (crossover in last {CFG.CROSS_LOOKBACK_DAYS} trading days): {eligible_symbols}")
    print(f"Alerts today: {len(alerts_df)}")
    print(f"ALLOW_ALERT_ON_CROSS_DATE={CFG.ALLOW_ALERT_ON_CROSS_DATE} | REARM_ON_REENTRY={CFG.REARM_ON_REENTRY} | REENTRY_MODE={CFG.REENTRY_MODE}")
    print("-" * 140)

    if alerts_df.empty:
        print("No alerts triggered today.")
        print("=" * 140 + "\n")
        return

    view = alerts_df.copy()

    # format a few numeric columns for readability
    for col in [
        "TodayClose", "EMA20",
        "WindowHigh_7D_preCross", "WindowLow_7D_preCross",
        "BreakDistance", "BreakPctOfPrice",
        "WindowRange", "BreakPctOfRange",
        "EmaDistance", "EmaPct"
    ]:
        if col in view.columns:
            view[col] = view[col].apply(_fmt)

    # Rank by BreakPctOfRange (bigger = stronger breakout)
    if "BreakPctOfRange" in alerts_df.columns:
        view["_rank"] = pd.to_numeric(alerts_df["BreakPctOfRange"], errors="coerce").fillna(0.0)
        view = view.sort_values(["Signal", "_rank"], ascending=[True, False]).drop(columns=["_rank"])

    cols = [
        "Signal", "Symbol", "EventDate",
        "TodayClose", "EMA20",
        "WindowHigh_7D_preCross", "WindowLow_7D_preCross",
        "BreakDistance", "BreakPctOfRange", "EmaDistance",
        "LatestCrossDate", "LatestCrossDirection",
        "ArmedBeforeEval", "ReentryToday"
    ]
    cols = [c for c in cols if c in view.columns]

    print(view[cols].head(max_rows).to_string(index=False))
    if len(view) > max_rows:
        print(f"\n... showing top {max_rows} of {len(view)} alerts.")
    print("=" * 140 + "\n")


def main():
    ensure_dirs(CFG.SYMBOLS_DIR, CFG.OUTPUT_DIR, os.path.dirname(CFG.DB_PATH))
    run_date = today_ymd()

    symbols_path = os.path.join(CFG.SYMBOLS_DIR, f"symbols_{run_date}.csv")
    if not os.path.exists(symbols_path):
        raise FileNotFoundError(f"Missing symbols file from Step 1: {symbols_path}")

    if not os.path.exists(CFG.DB_PATH):
        raise FileNotFoundError(f"Missing SQLite DB. Run Step 2 first: {CFG.DB_PATH}")

    symbols = read_df(symbols_path)["Symbol"].astype(str).tolist()
    total_symbols = len(symbols)

    conn = connect_db(CFG.DB_PATH)
    init_db(conn, wal_mode=CFG.SQLITE_WAL_MODE)
    init_state_tables(conn)

    rows = []
    alerts = []
    eligible_symbols = 0

    read_limit = max(CFG.YF_READ_LIMIT_ROWS, 120)

    for sym in symbols:
        df = read_daily_bars(conn, sym, limit_rows=read_limit)
        if df is None or df.empty:
            continue

        df = df.sort_values("Date").reset_index(drop=True)

        # Filter: must have crossover within last N trading days
        cross = find_latest_range_cross(df, ema_col="EMA20", lookback_days=CFG.CROSS_LOOKBACK_DAYS)
        if cross is None:
            continue

        eligible_symbols += 1

        latest_cross_date = cross["cross_date"]
        latest_cross_dir = cross["direction"]

        # Today's data
        today = df.iloc[-1]
        today_date = today["Date"].date()
        today_date_str = today_date.isoformat()

        close = float(today["Close"])
        ema20 = float(today["EMA20"])

        cross_dt = pd.to_datetime(latest_cross_date).date()

        # Toggle: allow/disallow alerts on CrossDate
        allowed_by_cross_toggle = (today_date >= cross_dt) if CFG.ALLOW_ALERT_ON_CROSS_DATE else (today_date > cross_dt)

        # --- Load state ---
        state = get_symbol_state(conn, sym)

        # Determine whether we need to refresh frozen window (only if CrossDate changed / missing window)
        need_refresh = (
            state is None or
            state["last_cross_date"] != latest_cross_date or
            state["window_high"] is None or
            state["window_low"] is None
        )

        if need_refresh:
            win = compute_anchored_window_before_cross(df, latest_cross_date, CFG.WINDOW_DAYS)
            if win is None:
                # Not enough pre-cross history
                rows.append({
                    "Symbol": sym,
                    "TodayDate": today_date_str,
                    "LatestCrossDate": latest_cross_date,
                    "LatestCrossDirection": latest_cross_dir,
                    "WindowReady": False,
                    "WindowHigh_7D_preCross": None,
                    "WindowLow_7D_preCross": None,
                    "Armed": None,
                    "ReentryToday": None,
                    "LongCandidate": False,
                    "ShortCandidate": False,
                })
                continue

            window_high, window_low = win

            # New crossover cycle => arm
            upsert_symbol_state(
                conn,
                symbol=sym,
                last_cross_date=latest_cross_date,
                last_cross_direction=latest_cross_dir,
                window_high=window_high,
                window_low=window_low,
                armed=1
            )
            state = get_symbol_state(conn, sym)

        window_high = float(state["window_high"])
        window_low = float(state["window_low"])
        
        armed_before_eval = int(state["armed"])

        # --- Rearm on re-entry ---
        reentry_today = False
        if CFG.REARM_ON_REENTRY and armed_before_eval == 0:
            if is_reentry(close, window_low, window_high):
                reentry_today = True
                set_armed(conn, sym, 1)
                state = get_symbol_state(conn, sym)
                armed_before_eval = int(state["armed"])  # now 1

        # --- Evaluate event conditions ---
        long_candidate = False
        short_candidate = False

        if allowed_by_cross_toggle and armed_before_eval == 1:
            long_candidate = (close > window_high) and (close > ema20)
            short_candidate = (close < window_low) and (close < ema20)

        # --- Metrics ---
        window_range = max(window_high - window_low, 1e-9)
        if long_candidate:
            break_distance = close - window_high
            ema_distance = close - ema20
        elif short_candidate:
            break_distance = window_low - close
            ema_distance = ema20 - close
        else:
            # still useful for near-miss analysis
            break_distance = max(close - window_high, window_low - close)
            ema_distance = close - ema20

        break_pct_price = break_distance / max(abs(close), 1e-9)
        break_pct_range = break_distance / window_range
        ema_pct = ema_distance / max(abs(close), 1e-9)

        rows.append({
            "Symbol": sym,
            "TodayDate": today_date_str,
            "TodayClose": close,
            "EMA20": ema20,
            "LatestCrossDate": latest_cross_date,
            "LatestCrossDirection": latest_cross_dir,
            "WindowReady": True,
            "WindowHigh_7D_preCross": window_high,
            "WindowLow_7D_preCross": window_low,
            "WindowRange": window_range,
            "ArmedBeforeEval": bool(armed_before_eval),
            "ReentryToday": bool(reentry_today),
            "AllowedByCrossToggle": bool(allowed_by_cross_toggle),
            "LongCandidate": bool(long_candidate),
            "ShortCandidate": bool(short_candidate),
        })

        # --- Fire alert ---
        if long_candidate or short_candidate:
            signal = "LONG" if long_candidate else "SHORT"

            # Disarm after alert
            set_armed(conn, sym, 0)
            set_alert_info(conn, sym, today_date_str, signal)

            alerts.append({
                "Symbol": sym,
                "EventDate": today_date_str,
                "Signal": signal,

                "TodayClose": close,
                "EMA20": ema20,

                "WindowHigh_7D_preCross": window_high,
                "WindowLow_7D_preCross": window_low,
                "WindowRange": window_range,

                "BreakDistance": break_distance,
                "BreakPctOfPrice": break_pct_price,
                "BreakPctOfRange": break_pct_range,

                "EmaDistance": ema_distance,
                "EmaPct": ema_pct,

                "LatestCrossDate": latest_cross_date,
                "LatestCrossDirection": latest_cross_dir,

                "ArmedBeforeEval": True,
                "ReentryToday": bool(reentry_today),
                "AllowedByCrossToggle": bool(allowed_by_cross_toggle),

                # Optional “stop references” (not trade advice; useful for future modules)
                "StopRef_Window": (window_high if signal == "LONG" else window_low),
                "StopRef_EMA20": ema20,
            })

    conn.close()

    out_all = pd.DataFrame(rows)
    if not out_all.empty:
        out_all = out_all.sort_values(
            ["LongCandidate", "ShortCandidate", "Symbol"],
            ascending=[False, False, True]
        )

    out_alerts = pd.DataFrame(alerts)

    out_all_path = os.path.join(CFG.OUTPUT_DIR, f"scan_all_{run_date}.csv")
    out_alerts_path = os.path.join(CFG.OUTPUT_DIR, f"scan_alerts_{run_date}.csv")

    save_df(out_all, out_all_path)
    save_df(out_alerts, out_alerts_path)

    # Console dashboard
    print_dashboard(
        run_date=run_date,
        total_symbols=total_symbols,
        eligible_symbols=eligible_symbols,
        alerts_df=out_alerts,
        max_rows=50
    )

    print("✅ Step 3 complete.")
    print(f"   All scan rows: {len(out_all)} -> {out_all_path}")
    print(f"   Alerts: {len(out_alerts)} -> {out_alerts_path}")


if __name__ == "__main__":
    main()
