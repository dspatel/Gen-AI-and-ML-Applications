import os
import time
import pandas as pd
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

from config import CFG
from utils.io_utils import ensure_dirs, today_ymd, read_df
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
)
from utils.indicators import find_latest_range_cross, add_ema20_columns
from utils.discord_notify import send_discord_message, format_alert_message
import numpy as np

def to_float(x):
    # Handles numpy scalars, python scalars, Series (1 element), etc.
    if hasattr(x, "iloc"):        # pandas Series/DataFrame column slice
        x = x.iloc[0]
    if isinstance(x, (np.generic,)):
        x = x.item()
    return float(x)

def compute_anchored_window_before_cross(df: pd.DataFrame, cross_date: str, window_days: int):
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


def get_session_times_chicago():
    import exchange_calendars as ecals
    import pandas as pd
    from zoneinfo import ZoneInfo

    tz_chi = ZoneInfo("America/Chicago")
    cal = ecals.get_calendar("XNYS")

    # Use a plain date string for schedule lookup to avoid tz-aware vs tz-naive slicing issues
    today_str = pd.Timestamp.now(tz=tz_chi).strftime("%Y-%m-%d")

    # --- Version-safe schedule retrieval ---
    if callable(getattr(cal, "schedule", None)):
        # Older API: schedule(...) returns a DataFrame
        sched = cal.schedule(start_date=today_str, end_date=today_str)
    else:
        # Newer API: schedule is a DataFrame indexed by session dates (tz-naive)
        sched = cal.schedule.loc[today_str:today_str]

    if sched.empty:
        raise RuntimeError("No NYSE trading session today (market closed).")

    row = sched.iloc[0]

    # Column names vary by version
    open_ts = row["market_open"] if "market_open" in row else row["open"]
    close_ts = row["market_close"] if "market_close" in row else row["close"]

    # open_ts/close_ts are typically tz-aware (UTC) in exchange_calendars; convert to Chicago
    market_open = pd.Timestamp(open_ts).to_pydatetime().astimezone(tz_chi)
    market_close = pd.Timestamp(close_ts).to_pydatetime().astimezone(tz_chi)

    return market_open, market_close



def _parse_hhmm(local_date: pd.Timestamp, hhmm: str, tz: ZoneInfo) -> datetime:
    """Create a timezone-aware datetime for local_date at HH:MM in tz."""
    hh, mm = hhmm.split(":")
    return datetime(
        year=local_date.year,
        month=local_date.month,
        day=local_date.day,
        hour=int(hh),
        minute=int(mm),
        second=0,
        tzinfo=tz,
    )


def get_live_run_window(market_open: datetime, market_close: datetime) -> tuple[datetime, datetime]:
    """Return (run_start, run_end) in CFG.TIMEZONE based on LIVE_SESSION_MODE."""
    tz = ZoneInfo(CFG.TIMEZONE)
    local_date = pd.Timestamp.now(tz=tz).date()
    local_date_ts = pd.Timestamp(local_date)

    mode = (CFG.LIVE_SESSION_MODE or "RTH").upper()
    pre_start = _parse_hhmm(local_date_ts, CFG.LIVE_PREMARKET_START, tz)
    post_end = _parse_hhmm(local_date_ts, CFG.LIVE_POSTMARKET_END, tz)

    if mode == "RTH":
        return market_open, market_close
    if mode == "PRE":
        return pre_start, market_open
    if mode == "POST":
        return market_close, post_end
    # ALL
    return pre_start, post_end


def load_universe(run_date: str) -> list[str]:
    # Prefer cross-universe file created by EOD scanner
    if getattr(CFG, "LIVE_UNIVERSE_PREFER_CROSS_FILE", True):
        cross_path = os.path.join(CFG.SYMBOLS_DIR, f"ema20_cross_{run_date}.csv")
        if os.path.exists(cross_path):
            df = read_df(cross_path)
            return df["Symbol"].astype(str).tolist()

    # Fallback to latest symbols_*.csv
    symbol_files = sorted([f for f in os.listdir(CFG.SYMBOLS_DIR) if f.startswith("symbols_") and f.endswith(".csv")])
    if not symbol_files:
        return []
    df = read_df(os.path.join(CFG.SYMBOLS_DIR, symbol_files[-1]))
    return df["Symbol"].astype(str).tolist()


def fetch_live_price_series(symbol: str, interval: str) -> pd.DataFrame:
    # 1 day intraday bars (yfinance)
    df = yf.download(symbol, period="1d", interval=interval, progress=False, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    # Normalize columns
    ts_col = "Datetime" if "Datetime" in df.columns else "Date"
    df.rename(columns={ts_col: "Timestamp"}, inplace=True)
    return df


def compute_live_daily_ema(daily_df: pd.DataFrame, live_price: float, live_high: float, live_low: float) -> tuple[float, float, float]:
    """Compute EMA20/EMA20_H/EMA20_L treating intraday as tentative today's values."""
    base = daily_df.copy()
    # Use last 60 rows for stability
    base = base.tail(60).copy()
    # Append synthetic 'today' row using live values
    today = pd.Timestamp.now(tz=ZoneInfo(CFG.TIMEZONE)).normalize()
    new_row = {
        "Date": today,
        "Open": float(base.iloc[-1]["Close"]) if len(base) else live_price,
        "High": float(live_high),
        "Low": float(live_low),
        "Close": float(live_price),
        "Volume": 0.0,
    }
    synth = pd.DataFrame([new_row])
    merged = pd.concat([base[["Date","Open","High","Low","Close","Volume"]], synth], ignore_index=True)
    merged = add_ema20_columns(merged, period=CFG.EMA_PERIOD)
    last = merged.iloc[-1]
    return float(last["EMA20"]), float(last["EMA20_H"]), float(last["EMA20_L"])


def main():
    ensure_dirs(CFG.SYMBOLS_DIR, CFG.OUTPUT_DIR, os.path.dirname(CFG.DB_PATH))
    tz = ZoneInfo(CFG.TIMEZONE)

    run_date = today_ymd()
    symbols = load_universe(run_date)
    if not symbols:
        raise FileNotFoundError("No symbols found to monitor (missing symbols_*.csv and ema20_cross_*.csv).")

    conn = connect_db(CFG.DB_PATH)
    init_db(conn, wal_mode=CFG.SQLITE_WAL_MODE)
    init_state_tables(conn)
    init_alerts_log(conn)

    market_open, market_close = get_session_times_chicago()
    now = datetime.now(tz)

    if market_open is None:
        print(f"Market closed today (XNYS) in {CFG.TIMEZONE}. Exiting.")
        return

    run_start, run_end = get_live_run_window(market_open, market_close)

    if getattr(CFG, "LIVE_AUTO_WAIT_FOR_SESSION_START", True) and now < run_start:
        wait_sec = (run_start - now).total_seconds()
        print(f"Waiting for session start at {run_start} ({CFG.TIMEZONE}). Sleeping {int(wait_sec)} seconds...")
        time.sleep(max(1, int(wait_sec)))

    print(f"LIVE TRACKER STARTED | {run_date} | Symbols: {len(symbols)} | Interval: {CFG.LIVE_INTERVAL} | TZ: {CFG.TIMEZONE}")
    print(f"Market session (RTH): {market_open} → {market_close}")
    print(f"Live run window ({CFG.LIVE_SESSION_MODE}): {run_start} → {run_end}")

    while True:
        now = datetime.now(tz)
        if getattr(CFG, "LIVE_AUTO_STOP_AFTER_SESSION_END", True) and now >= run_end:
            print(f"Live run window ended at {run_end}. Stopping live tracker.")
            break

        # If running PRE mode and we crossed into RTH (or other transitions), we stop at run_end.
        # Also, if running POST and started before market_close, wait until market_close.
        if now < run_start:
            time.sleep(5)
            continue

        for sym in symbols:
            # Load daily bars from DB
            daily = read_daily_bars(conn, sym, limit_rows=CFG.YF_READ_LIMIT_ROWS)
            if daily is None or daily.empty:
                continue
            daily = daily.sort_values("Date").reset_index(drop=True)

            # Determine latest cross (daily)
            cross = find_latest_range_cross(daily, ema_col="EMA20", lookback_days=CFG.CROSS_LOOKBACK_DAYS)
            if cross is None:
                continue

            latest_cross_date = cross["cross_date"]
            latest_cross_dir = cross["direction"]

            # Ensure state exists with frozen windows
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
                win7 = compute_anchored_window_before_cross(daily, latest_cross_date, CFG.WINDOW_DAYS)
                win21 = compute_anchored_window_before_cross(daily, latest_cross_date, CFG.WINDOW_DAYS_LONG)
                if win7 is None or win21 is None:
                    continue
                upsert_symbol_state(
                    conn,
                    sym,
                    latest_cross_date,
                    latest_cross_dir,
                    win7[0], win7[1],
                    1,
                    window_high_7=win7[0], window_low_7=win7[1],
                    window_high_21=win21[0], window_low_21=win21[1],
                )
                state = get_symbol_state(conn, sym)

            window_high_7 = float(state.get("window_high_7") or state.get("window_high"))
            window_low_7 = float(state.get("window_low_7") or state.get("window_low"))
            window_high_21 = float(state.get("window_high_21") or window_high_7)
            window_low_21 = float(state.get("window_low_21") or window_low_7)
            armed = int(state.get("armed", 1))

            # Get intraday series and current price/high/low
            intra = fetch_live_price_series(sym, CFG.LIVE_INTERVAL)
            if intra.empty:
                continue

            # Convert timestamps to Chicago time if tz-aware
            if pd.api.types.is_datetime64_any_dtype(intra["Timestamp"]):
                ts = intra["Timestamp"]
                if ts.dt.tz is None:
                    # yfinance sometimes returns naive; assume UTC then convert
                    intra["Timestamp"] = ts.dt.tz_localize("UTC").dt.tz_convert(tz)
                else:
                    intra["Timestamp"] = ts.dt.tz_convert(tz)


            tz_chi = ZoneInfo("America/Chicago")
            # Use latest bar close
            latest_bar = intra.iloc[-2]   # last completed
            candle_ts = intra.index[-2]
            # Ensure tz-aware then convert to Chicago
            candle_ts = pd.Timestamp(candle_ts)
            if candle_ts.tzinfo is None:
                # yfinance often returns tz-naive; assume UTC then convert
                candle_ts = candle_ts.tz_localize("UTC")
            candle_ts_chi = candle_ts.tz_convert(tz_chi)

            candle_time_str = candle_ts_chi.strftime("%Y-%m-%d %H:%M:%S %Z")
            live_low = to_float(intra["Low"].min())
            live_high = to_float(intra["High"].max())

            # If latest_bar is a Series, this is fine:
            live_price = to_float(latest_bar["Close"])

            # Compute live EMA values based on tentative today's bar
            ema20, ema20_h, ema20_l = compute_live_daily_ema(daily, live_price, live_high, live_low)

            # Rearm on re-entry (using 7D box)
            if CFG.REARM_ON_REENTRY and armed == 0 and is_reentry(live_price, window_low_7, window_high_7):
                set_armed(conn, sym, True)
                armed = 1

            # Allowed on cross date toggle (date-only)
            cross_dt = pd.to_datetime(latest_cross_date).date()
            today_dt = pd.Timestamp.now(tz=tz).date()
            allowed_by_cross = (today_dt >= cross_dt) if CFG.ALLOW_ALERT_ON_CROSS_DATE else (today_dt > cross_dt)

            long_signal = bool(allowed_by_cross and armed == 1 and live_price > window_high_7 and live_price > ema20)
            short_signal = bool(allowed_by_cross and armed == 1 and live_price < window_low_7 and live_price < ema20)
            if not (long_signal or short_signal):
                continue

            signal = "LONG" if long_signal else "SHORT"

            # Metrics
            rng7 = max(window_high_7 - window_low_7, 1e-9)
            rng21 = max(window_high_21 - window_low_21, 1e-9)
            break_dist_7 = (live_price - window_high_7) if long_signal else (window_low_7 - live_price)
            break_dist_21 = (live_price - window_high_21) if long_signal else (window_low_21 - live_price)
            break_pct_7 = break_dist_7 / rng7
            break_pct_21 = break_dist_21 / rng21
            ema_dist = (live_price - ema20) if long_signal else (ema20 - live_price)

            alert = {
                "Symbol": sym,
                "EventDate": today_dt.isoformat(),
                "EventTime": datetime.now(tz).strftime("%H:%M:%S"),
                "Signal": signal,
                "TodayClose": live_price,
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
                "CandleTime" : candle_time_str,
                "CandleTimestamp" : candle_ts_chi.isoformat()
            }

            inserted = insert_alert_log(conn, alert, source="LIVE")

            if inserted:
                # Disarm + mark alert info
                set_armed(conn, sym, False)
                set_alert_info(conn, sym, today_dt.isoformat(), signal)

                if CFG.DISCORD_ENABLED and CFG.DISCORD_WEBHOOK_URL and CFG.DISCORD_SEND_LIVE_ALERTS:
                    msg = format_alert_message(alert, env=CFG.DISCORD_ENV)
                    send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)

        time.sleep(max(5, int(CFG.LIVE_POLL_SECONDS)))

    conn.close()


if __name__ == "__main__":
    main()
