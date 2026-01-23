from __future__ import annotations

import os
import time
from datetime import datetime, date
from typing import Any, Dict, Optional, Tuple, List

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

from config import CFG
from utils.io_utils import ensure_dirs, find_latest_file
from utils.discord_notify import send_discord_message
from utils.sqlite_store import (
    connect_db,
    init_db,
    init_state_tables,
    init_alerts_log,
    get_symbol_state,
    set_armed,
    set_alert_info,
    insert_alert_log,
)

TZ = ZoneInfo(getattr(CFG, "TIMEZONE", "America/Chicago"))

def _now_local() -> datetime:
    return datetime.now(TZ)

def _to_float(x) -> Optional[float]:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        return float(x)
    except Exception:
        return None

def load_live_symbols() -> Tuple[List[str], str]:
    """
    Live tracker watches symbols that are already cross-eligible (Step 3 produced ema20_cross_*.csv).
    Fallback: use the latest symbols_*.csv universe if no cross file exists.
    """
    ensure_dirs(CFG.SYMBOLS_DIR, CFG.OUTPUT_DIR)

    cross = find_latest_file(CFG.SYMBOLS_DIR, prefix="ema20_cross_", suffix=".csv")
    if cross is not None:
        df = pd.read_csv(cross)
        col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        syms = [str(s).strip().upper() for s in df[col].dropna().tolist()]
        return syms, cross

    latest = find_latest_file(CFG.SYMBOLS_DIR, prefix="symbols_", suffix=".csv")
    if latest is None:
        raise FileNotFoundError(f"No ema20_cross_*.csv or symbols_*.csv in {CFG.SYMBOLS_DIR}. Run Step 1/3 first.")
    df = pd.read_csv(latest)
    col = "Symbol" if "Symbol" in df.columns else ("Ticker" if "Ticker" in df.columns else df.columns[0])
    syms = [str(s).strip().upper() for s in df[col].dropna().tolist()]
    return syms, latest

def fetch_intraday(symbol: str, interval: str) -> pd.DataFrame:
    """
    Returns intraday OHLCV for today (or last session if outside hours).
    yfinance may return tz-naive index; we normalize to tz-aware in America/Chicago.
    """
    t = yf.Ticker(symbol)
    df = t.history(period="1d", interval=interval, prepost=bool(getattr(CFG, "LIVE_INCLUDE_PREPOST", False)))
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    # yfinance uses 'Datetime' or 'Date'
    ts_col = "Datetime" if "Datetime" in df.columns else ("Date" if "Date" in df.columns else df.columns[0])
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce", utc=True)
    df = df.dropna(subset=[ts_col]).rename(columns={ts_col: "Timestamp"})
    df["Timestamp"] = df["Timestamp"].dt.tz_convert(TZ)
    return df

def format_live_alert(alert: Dict[str, Any]) -> str:
    title = f"🚨 EMA20 Anchored Breakout ({getattr(CFG, 'DISCORD_ENV', 'PROD')})"
    lines = [
        title,
        "",
        f"Symbol: {alert['Symbol']}",
        f"Signal: {alert['Signal']}",
        f"Event Date: {alert['EventDate']}",
        f"Candle Time: {alert.get('CandleTime','')}",
        f"Event Time: {alert.get('EventTime','')}",
        "",
        f"Price/Close: {alert.get('TodayClose')}",
        f"EMA20: {alert.get('EMA20')} | EMA20_H: {alert.get('EMA20_H')} | EMA20_L: {alert.get('EMA20_L')}",
        "",
    ]
    d1 = alert.get("PrimaryWindowDaysUsed")
    if d1:
        lines.append(f"{d1}D Window High/Low: {alert.get(f'WindowHigh_{d1}D_preCross')} / {alert.get(f'WindowLow_{d1}D_preCross')}")
    d2 = alert.get("SecondaryWindowDaysUsed")
    if d2:
        lines.append(f"{d2}D Window High/Low: {alert.get(f'WindowHigh_{d2}D_preCross')} / {alert.get(f'WindowLow_{d2}D_preCross')}")
    lines.append("")
    if d1:
        lines.append(f"Break % of {d1}D Range: {alert.get(f'BreakPct_{d1}D')}")
    if d2:
        lines.append(f"Break % of {d2}D Range: {alert.get(f'BreakPct_{d2}D')}")
    lines.append(f"EMA Distance: {alert.get('EmaDistance')}")
    lines.append("")
    lines.append(f"Latest Cross: {alert.get('LatestCrossDate')} ({alert.get('LatestCrossDirection')})")
    return "\n".join(lines)

def _startup_banner_dict(symbols_count: int, universe_src: str, interval: str, poll_seconds: int, use_completed: bool) -> Dict[str, Any]:
    return {
        "date": date.today().isoformat(),
        "timezone": CFG.TIMEZONE,
        "universe": os.path.basename(universe_src) if universe_src else "",
        "symbols": symbols_count,
        "interval": interval,
        "poll_seconds": poll_seconds,
        "use_last_completed_bar": use_completed,
        "ema_period": CFG.EMA_PERIOD,
        "cross_lookback_days": CFG.CROSS_LOOKBACK_DAYS,
        "primary_window_days": CFG.WINDOW_DAYS_PRIMARY,
        "secondary_window_enabled": CFG.ENABLE_SECONDARY_WINDOW,
        "secondary_window_days": CFG.WINDOW_DAYS_SECONDARY if CFG.ENABLE_SECONDARY_WINDOW else None,
        "rearm_on_reentry": CFG.REARM_ON_REENTRY,
        "reentry_mode": CFG.REENTRY_MODE,
        "discord_enabled": CFG.DISCORD_ENABLED and bool(CFG.DISCORD_WEBHOOK_URL),
        "discord_env": CFG.DISCORD_ENV,
    }


def print_start_banner(universe_src: str, symbols_count: int, interval: str, poll_seconds: int, use_completed: bool) -> None:
    b = _startup_banner_dict(symbols_count, universe_src, interval, poll_seconds, use_completed)
    print("=" * 100)
    print_start_banner(universe_src=src, symbols_count=len(symbols), interval=interval, poll_seconds=poll_seconds, use_completed=use_completed)
    send_startup_banner_discord(universe_src=src, symbols_count=len(symbols), interval=interval, poll_seconds=poll_seconds, use_completed=use_completed)
    print("-" * 100)
    print("STRATEGY")
    print(f"  EMA Period: {b['ema_period']}")
    print(f"  Cross Lookback: {b['cross_lookback_days']} trading days")
    print(f"  Primary Window: {b['primary_window_days']}D")
    if b['secondary_window_enabled']:
        print(f"  Secondary Window: {b['secondary_window_days']}D (enabled)")
    else:
        print("  Secondary Window: OFF")
    print(f"  Rearm on Re-entry: {b['rearm_on_reentry']} | Reentry Mode: {b['reentry_mode']}")
    print("LIVE")
    print(f"  Use last completed bar: {b['use_last_completed_bar']}")
    print(f"  Session Mode: {CFG.LIVE_SESSION_MODE}")
    print("NOTIFICATIONS")
    print(f"  Discord enabled: {b['discord_enabled']} | Env: {b['discord_env']}")
    print("=" * 100)


def send_startup_banner_discord(universe_src: str, symbols_count: int, interval: str, poll_seconds: int, use_completed: bool) -> None:
    if not (CFG.DISCORD_ENABLED and CFG.DISCORD_WEBHOOK_URL and getattr(CFG, "DISCORD_SEND_STARTUP_BANNER", True)):
        return
    b = _startup_banner_dict(symbols_count, universe_src, interval, poll_seconds, use_completed)
    msg = (
        f"🟦 **EMA20 Live Tracker STARTED ({b['discord_env']})**\n\n"
        f"**Date:** {b['date']}\n"
        f"**TZ:** {b['timezone']}\n"
        f"**Universe:** `{b['universe']}` ({b['symbols']} symbols)\n\n"
        f"**Strategy**\n"
        f"- EMA: {b['ema_period']}\n"
        f"- Cross lookback: {b['cross_lookback_days']} trading days\n"
        f"- Primary window: {b['primary_window_days']}D\n"
        f"- Secondary window: {'ON' if b['secondary_window_enabled'] else 'OFF'}"
        f"{' (' + str(b['secondary_window_days']) + 'D)' if b['secondary_window_enabled'] else ''}\n"
        f"- Rearm on re-entry: {b['rearm_on_reentry']} ({b['reentry_mode']})\n\n"
        f"**Live**\n"
        f"- Interval: {b['interval']}\n"
        f"- Poll: {b['poll_seconds']}s\n"
        f"- Last completed bar: {b['use_last_completed_bar']}\n"
        f"- Session mode: {CFG.LIVE_SESSION_MODE}"
    )
    send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)


def send_shutdown_banner_discord(reason: str) -> None:
    if not (CFG.DISCORD_ENABLED and CFG.DISCORD_WEBHOOK_URL and getattr(CFG, "DISCORD_SEND_SHUTDOWN_BANNER", True)):
        return
    tz = ZoneInfo(CFG.TIMEZONE)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    msg = f"🟥 **EMA20 Live Tracker STOPPED ({CFG.DISCORD_ENV})**\n\n**Time:** {now}\n**Reason:** {reason}"
    send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)

def main() -> None:
    ensure_dirs(CFG.SYMBOLS_DIR, CFG.OUTPUT_DIR, os.path.dirname(CFG.DB_PATH))
    conn = connect_db(CFG.DB_PATH)
    init_db(conn, wal_mode=bool(getattr(CFG, "SQLITE_WAL_MODE", True)))
    init_state_tables(conn)
    init_alerts_log(conn)

    symbols, src = load_live_symbols()

    interval = getattr(CFG, "LIVE_INTERVAL", "5m")
    poll_seconds = int(getattr(CFG, "LIVE_POLL_SECONDS", 60))
    use_completed = bool(getattr(CFG, "LIVE_USE_LAST_COMPLETED_BAR", True))

    print(f"LIVE TRACKER STARTED | {date.today().isoformat()} | Symbols: {len(symbols)} | Interval: {interval} | TZ: {TZ}")
    print(f"Universe source: {os.path.basename(src)}")

    while True:
        now = _now_local()
        event_date = now.date().isoformat()
        event_time = now.strftime("%H:%M:%S")

        for sym in symbols:
            state = get_symbol_state(conn, sym)
            if state is None:
                # If state isn't prepared yet, skip (run Step 3 or morning prep first)
                continue

            d1 = int(state.get("window_days_primary") or CFG.WINDOW_DAYS_PRIMARY)
            wh1 = _to_float(state.get("window_high_primary"))
            wl1 = _to_float(state.get("window_low_primary"))
            if wh1 is None or wl1 is None:
                continue

            d2 = None
            wh2 = None
            wl2 = None
            if getattr(CFG, "ENABLE_SECONDARY_WINDOW", False):
                d2 = int(state.get("window_days_secondary") or CFG.WINDOW_DAYS_SECONDARY)
                wh2 = _to_float(state.get("window_high_secondary"))
                wl2 = _to_float(state.get("window_low_secondary"))

            armed = int(state.get("armed", 1))
            cross_date = state.get("last_cross_date")
            cross_dir = state.get("last_cross_direction")

            intra = fetch_intraday(sym, interval)
            if intra.empty:
                continue

            # choose bar
            idx = -2 if (use_completed and len(intra) >= 2) else -1
            bar = intra.iloc[idx]
            candle_ts = bar["Timestamp"]
            if pd.isna(candle_ts):
                continue

            candle_time = pd.Timestamp(candle_ts).strftime("%Y-%m-%d %H:%M:%S %Z")

            close = float(bar["Close"])
            high = float(intra["High"].max())
            low = float(intra["Low"].min())

            # Use last known EMA values from DB? Live tracker does not recompute EMA from intraday.
            # We store EMA from daily bars in step2; state doesn't include EMA. For live alerts, we only need EMA20 band from daily last bar.
            # Pull from daily_bars quickly via SQL (fast).
            daily = pd.read_sql_query(
                "SELECT date, ema20, ema20_h, ema20_l FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT 1;",
                conn,
                params=(sym,),
            )
            if daily.empty:
                continue
            ema20 = _to_float(daily.iloc[0].get("ema20"))
            ema20_h = _to_float(daily.iloc[0].get("ema20_h"))
            ema20_l = _to_float(daily.iloc[0].get("ema20_l"))
            if ema20 is None:
                continue

            long_signal = (close > wh1) and (close > ema20)
            short_signal = (close < wl1) and (close < ema20)
            signal = "LONG" if long_signal else ("SHORT" if short_signal else None)

            # Rearm on re-entry
            if getattr(CFG, "REARM_ON_REENTRY", True) and armed == 0:
                if wl1 <= close <= wh1:
                    set_armed(conn, sym, 1)
                    armed = 1

            if signal is None or armed != 1:
                continue

            rng1 = max(wh1 - wl1, 1e-9)
            break_dist1 = (close - wh1) if long_signal else (wl1 - close)
            break_pct1 = break_dist1 / rng1

            break_pct2 = None
            if d2 is not None and wh2 is not None and wl2 is not None:
                rng2 = max(wh2 - wl2, 1e-9)
                break_dist2 = (close - wh2) if long_signal else (wl2 - close)
                break_pct2 = break_dist2 / rng2

            ema_dist = (close - ema20) if long_signal else (ema20 - close)

            alert: Dict[str, Any] = {
                "Symbol": sym,
                "Signal": signal,
                "EventDate": event_date,
                "EventTime": event_time,
                "CandleTime": candle_time,

                "TodayClose": close,
                "EMA20": ema20,
                "EMA20_H": ema20_h,
                "EMA20_L": ema20_l,

                "LatestCrossDate": cross_date,
                "LatestCrossDirection": cross_dir,

                "PrimaryWindowDaysUsed": d1,
                f"WindowHigh_{d1}D_preCross": wh1,
                f"WindowLow_{d1}D_preCross": wl1,
                f"BreakPct_{d1}D": break_pct1,
                "EmaDistance": ema_dist,
            }
            if d2 is not None and wh2 is not None and wl2 is not None:
                alert.update({
                    "SecondaryWindowDaysUsed": d2,
                    f"WindowHigh_{d2}D_preCross": wh2,
                    f"WindowLow_{d2}D_preCross": wl2,
                    f"BreakPct_{d2}D": break_pct2,
                })

            inserted = insert_alert_log(conn, alert, source="LIVE")
            if not inserted:
                continue

            # disarm + book-keeping
            set_armed(conn, sym, 0)
            set_alert_info(conn, sym, event_date, signal)

            if getattr(CFG, "DISCORD_ENABLED", False) and getattr(CFG, "DISCORD_WEBHOOK_URL", "") and getattr(CFG, "DISCORD_SEND_LIVE_ALERTS", True):
                msg = format_live_alert(alert)
                send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)

        time.sleep(poll_seconds)

if __name__ == "__main__":
    main()