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


def _startup_banner_text(run_date: str, symbols: List[str], universe_src: str) -> str:
    """Human-readable run header used in console + optional Discord."""
    parts = [
        f"🟦 EMA20 LIVE Tracker STARTED ({getattr(CFG, 'DISCORD_ENV', 'PROD')})",
        "",
        f"Date: {run_date}",
        f"TZ: {getattr(CFG, 'TIMEZONE', 'America/Chicago')}",
        f"Universe: {os.path.basename(universe_src)} ({len(symbols)} symbols)",
        "",
        "Strategy",
        f"- EMA period: {getattr(CFG, 'EMA_PERIOD', 20)}",
        f"- Cross lookback: {getattr(CFG, 'CROSS_LOOKBACK_DAYS', 30)} trading days",
        f"- Primary window: {getattr(CFG, 'WINDOW_DAYS_PRIMARY', 35)}D",
        f"- Secondary window: {'ON' if getattr(CFG, 'ENABLE_SECONDARY_WINDOW', True) else 'OFF'}" + (
            f" ({getattr(CFG, 'WINDOW_DAYS_SECONDARY', 21)}D)" if getattr(CFG, 'ENABLE_SECONDARY_WINDOW', True) else ""
        ),
        f"- Rearm on re-entry: {getattr(CFG, 'REARM_ON_REENTRY', True)} ({getattr(CFG, 'REENTRY_MODE', 'strict')})",
        "",
        "Live",
        f"- Interval: {getattr(CFG, 'LIVE_INTERVAL', '5m')}",
        f"- Poll seconds: {getattr(CFG, 'LIVE_POLL_SECONDS', 60)}",
        f"- Use last completed bar: {getattr(CFG, 'LIVE_USE_LAST_COMPLETED_BAR', True)}",
        f"- Session mode: {getattr(CFG, 'LIVE_SESSION_MODE', 'RTH')}",
    ]
    return "\n".join(parts)


def _shutdown_banner_text(run_date: str) -> str:
    now = _now_local().strftime("%Y-%m-%d %H:%M:%S %Z")
    return "\n".join([
        f"🟥 EMA20 LIVE Tracker STOPPED ({getattr(CFG, 'DISCORD_ENV', 'PROD')})",
        "",
        f"Run date: {run_date}",
        f"Stopped at: {now}",
    ])

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

    run_date = date.today().isoformat()

    # "No confusion" startup banner (console + optional Discord)
    banner = _startup_banner_text(run_date, symbols, src)
    print("\n" + "=" * 90)
    print(banner)
    print("=" * 90 + "\n")
    if (
        getattr(CFG, "DISCORD_ENABLED", False)
        and getattr(CFG, "DISCORD_WEBHOOK_URL", "")
        and getattr(CFG, "DISCORD_SEND_STARTUP_BANNER", True)
    ):
        send_discord_message(CFG.DISCORD_WEBHOOK_URL, banner)

    try:
        while True:
            now = _now_local()
            event_date = now.date().isoformat()
            event_time = now.strftime("%H:%M:%S")

            for sym in symbols:
                state = get_symbol_state(conn, sym)
                if state is None:
                    # If state isn't prepared yet, skip (run Step 3 / morning prep first)
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
                if candle_ts is None or (hasattr(candle_ts, '__float__') and getattr(candle_ts, 'isna', lambda: False)()):
                    continue

                candle_time = pd.Timestamp(candle_ts).strftime("%Y-%m-%d %H:%M:%S %Z")

                close = float(bar["Close"])

                # Pull latest daily EMA band from DB (step2 caches it)
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

                if (
                    getattr(CFG, "DISCORD_ENABLED", False)
                    and getattr(CFG, "DISCORD_WEBHOOK_URL", "")
                    and getattr(CFG, "DISCORD_SEND_LIVE_ALERTS", True)
                ):
                    msg = format_live_alert(alert)
                    send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)

            # Poll cycle sleep (outside symbol loop)
            time.sleep(poll_seconds)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt: stopping live tracker...\n")
    finally:
        if (
            getattr(CFG, "DISCORD_ENABLED", False)
            and getattr(CFG, "DISCORD_WEBHOOK_URL", "")
            and getattr(CFG, "DISCORD_SEND_SHUTDOWN_BANNER", True)
        ):
            send_discord_message(CFG.DISCORD_WEBHOOK_URL, _shutdown_banner_text(run_date))
        conn.close()

if __name__ == "__main__":
    main()
