from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, List

import pandas as pd

from config import CFG
from utils.io_utils import ensure_dirs, find_latest_file
from utils.indicators import add_ema20_columns, find_latest_range_cross, compute_ema20_cross_stats
from utils.discord_notify import send_discord_message, send_discord_file, format_alerts_table
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
    finalize_alerts_day_ohlc,
    init_eod_scan_alerts,
    insert_eod_scan_alert,
    read_eod_scan_alerts_for_date,
)

def _today_str_local() -> str:
    # EOD runner: use local today (America/Chicago handled by runner).
    return datetime.now().date().isoformat()

def _to_float(x) -> Optional[float]:
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        return float(x)
    except Exception:
        return None

def compute_anchored_window_before_cross(df: pd.DataFrame, cross_date: str, window_days: int) -> Optional[Tuple[float, float]]:
    """
    Freeze a window of `window_days` trading days immediately BEFORE cross_date.
    cross_date row itself is excluded.
    df must have Date, High, Low sorted ascending.
    """
    if df.empty:
        return None
    df = df.sort_values("Date").reset_index(drop=True)
    cross_dt = pd.to_datetime(cross_date).date()
    dates = pd.to_datetime(df["Date"]).dt.date
    # find index of cross date row
    idxs = [i for i, d in enumerate(dates) if d == cross_dt]
    if not idxs:
        return None
    i_cross = idxs[-1]
    start = i_cross - window_days
    end = i_cross  # exclude cross bar
    if start < 0:
        return None
    window = df.iloc[start:end]
    if window.empty:
        return None
    return float(window["High"].max()), float(window["Low"].min())

def load_universe_symbols() -> Tuple[List[str], str]:
    """Return symbols list and the file path used."""
    ensure_dirs(CFG.SYMBOLS_DIR, CFG.OUTPUT_DIR)
    latest = find_latest_file(CFG.SYMBOLS_DIR, prefix="symbols_", suffix=".csv")
    if latest is None:
        raise FileNotFoundError(f"No symbols_*.csv found in {CFG.SYMBOLS_DIR}. Run Step 1 first.")
    df = pd.read_csv(latest)
    # common columns from TradingView export: 'Symbol' or 'Ticker'
    col = "Symbol" if "Symbol" in df.columns else ("Ticker" if "Ticker" in df.columns else df.columns[0])
    symbols = [str(s).strip().upper() for s in df[col].dropna().tolist()]
    # basic cleanup
    symbols = [s for s in symbols if s and s != "NAN"]
    return symbols, latest

def evaluate_eod_for_symbol(
    conn,
    symbol: str,
    asof_date: str,
) -> Optional[Dict[str, Any]]:
    df = read_daily_bars(conn, symbol, limit_rows=int(getattr(CFG, "YF_READ_LIMIT_ROWS", 260)))
    if df is None or df.empty or len(df) < (CFG.EMA_PERIOD + 5):
        return None

    # ensure EMA columns exist (Step2 should store them, but keep safe)
    if "EMA20" not in df.columns or df["EMA20"].isna().all():
        df = add_ema20_columns(df, period=int(CFG.EMA_PERIOD))

    # latest cross within lookback
    cross = find_latest_range_cross(df, ema_col="EMA20", lookback_days=int(CFG.CROSS_LOOKBACK_DAYS))
    if cross is None:
        return None

    cross_date = cross["cross_date"]
    cross_dir = cross["direction"]

    # state load + refresh if needed
    state = get_symbol_state(conn, symbol)
    primary_days = int(CFG.WINDOW_DAYS_PRIMARY)
    secondary_enabled = bool(CFG.ENABLE_SECONDARY_WINDOW)
    secondary_days = int(CFG.WINDOW_DAYS_SECONDARY) if secondary_enabled else None

    need_refresh = (
        state is None
        or state.get("last_cross_date") != cross_date
        or state.get("window_days_primary") != primary_days
        or (secondary_enabled and state.get("window_days_secondary") != secondary_days)
        or state.get("window_high_primary") is None
        or state.get("window_low_primary") is None
        or (secondary_enabled and (state.get("window_high_secondary") is None or state.get("window_low_secondary") is None))
    )

    if need_refresh:
        win_primary = compute_anchored_window_before_cross(df, cross_date, primary_days)
        if win_primary is None:
            return None
        if secondary_enabled:
            win_secondary = compute_anchored_window_before_cross(df, cross_date, secondary_days)
            if win_secondary is None:
                return None
        else:
            win_secondary = None

        upsert_symbol_state(
            conn,
            symbol,
            cross_date,
            cross_dir,
            armed=1,
            window_days_primary=primary_days,
            window_high_primary=win_primary[0],
            window_low_primary=win_primary[1],
            window_days_secondary=(secondary_days if secondary_enabled else None),
            window_high_secondary=(win_secondary[0] if secondary_enabled else None),
            window_low_secondary=(win_secondary[1] if secondary_enabled else None),
        )
        state = get_symbol_state(conn, symbol)

    # use the last available bar as "asof"
    last = df.iloc[-1]
    event_date = pd.to_datetime(last["Date"]).date().isoformat()

    # if testing asof, ensure we evaluate that date if present
    if asof_date and asof_date != event_date:
        # find row matching asof_date
        mask = pd.to_datetime(df["Date"]).dt.date.astype(str) == asof_date
        if mask.any():
            last = df[mask].iloc[-1]
            event_date = asof_date

    # EMA20 cross stats over recent trading days (feature engineering)
    try:
        lookback_td = int(getattr(CFG, "EMA20_CROSS_COUNT_LOOKBACK_TD", 30))
        include_day = bool(getattr(CFG, "EMA20_CROSS_COUNT_INCLUDE_EVENT_DAY", True))
        df_stats = df.copy()
        df_stats["Date"] = pd.to_datetime(df_stats["Date"], errors="coerce").dt.date
        df_stats = df_stats[df_stats["Date"].astype(str) <= event_date]
        cross_stats = compute_ema20_cross_stats(df_stats, asof_date=event_date, lookback_td=lookback_td, include_event_day=include_day)
    except Exception:
        cross_stats = {"lookback_td": int(getattr(CFG, "EMA20_CROSS_COUNT_LOOKBACK_TD", 30)),
                       "count_total": None, "count_bull": None, "count_bear": None,
                       "days_since_last_cross": None, "cross_density": None}

    close = float(last["Close"])
    o = float(last.get("Open", close))
    h = float(last.get("High", close))
    l = float(last.get("Low", close))
    ema20 = float(last["EMA20"])
    ema20_h = float(last.get("EMA20_H", ema20))
    ema20_l = float(last.get("EMA20_L", ema20))

    d1 = int(state.get("window_days_primary") or primary_days)
    wh1 = float(state.get("window_high_primary"))
    wl1 = float(state.get("window_low_primary"))

    d2 = None
    wh2 = None
    wl2 = None
    if secondary_enabled:
        d2 = int(state.get("window_days_secondary") or secondary_days)
        wh2 = _to_float(state.get("window_high_secondary"))
        wl2 = _to_float(state.get("window_low_secondary"))

    armed = int(state.get("armed", 1))

    # EOD signal condition (uses PRIMARY window)
    long_signal = (close > wh1) and (close > ema20)
    short_signal = (close < wl1) and (close < ema20)

    signal = "LONG" if long_signal else ("SHORT" if short_signal else None)

    # Rearm logic: if disarmed and re-entry into primary window happens, rearm
    if CFG.REARM_ON_REENTRY and armed == 0:
        in_range = (wl1 <= close <= wh1)
        if in_range:
            set_armed(conn, symbol, 1)
            armed = 1

    if signal is None:
        return {
            "Symbol": symbol,
            "EventDate": event_date,
            "Close": close,
            "EMA20": ema20,
            "EMA20_H": ema20_h,
            "EMA20_L": ema20_l,
            "Ema20CrossLookbackTD": cross_stats.get("lookback_td"),
            "Ema20CrossCountTotal": cross_stats.get("count_total"),
            "Ema20CrossCountBull": cross_stats.get("count_bull"),
            "Ema20CrossCountBear": cross_stats.get("count_bear"),
            "Ema20CrossDaysSinceLast": cross_stats.get("days_since_last_cross"),
            "Ema20CrossDensity": cross_stats.get("cross_density"),
            "LatestCrossDate": cross_date,
            "LatestCrossDirection": cross_dir,
            "PrimaryWindowDaysUsed": d1,
            f"WindowHigh_{d1}D_preCross": wh1,
            f"WindowLow_{d1}D_preCross": wl1,
            "SecondaryWindowDaysUsed": d2,
            (f"WindowHigh_{d2}D_preCross" if d2 else "WindowHigh_Secondary_preCross"): wh2,
            (f"WindowLow_{d2}D_preCross" if d2 else "WindowLow_Secondary_preCross"): wl2,
            "Armed": armed,
            "Signal": "",
        }

    # Only alert if armed
    if armed != 1:
        return {
            "Symbol": symbol,
            "EventDate": event_date,
            "Close": close,
            "EMA20": ema20,
            "EMA20_H": ema20_h,
            "EMA20_L": ema20_l,
            "LatestCrossDate": cross_date,
            "LatestCrossDirection": cross_dir,
            "PrimaryWindowDaysUsed": d1,
            f"WindowHigh_{d1}D_preCross": wh1,
            f"WindowLow_{d1}D_preCross": wl1,
            "Armed": armed,
            "Signal": f"{signal} (blocked: disarmed)",
        }

    # compute metrics
    rng1 = max(wh1 - wl1, 1e-9)
    break_dist1 = (close - wh1) if long_signal else (wl1 - close)
    break_pct1 = break_dist1 / rng1

    break_pct2 = None
    if secondary_enabled and wh2 is not None and wl2 is not None:
        rng2 = max(wh2 - wl2, 1e-9)
        break_dist2 = (close - wh2) if long_signal else (wl2 - close)
        break_pct2 = break_dist2 / rng2

    ema_dist = (close - ema20) if long_signal else (ema20 - close)

    alert = {
        "Symbol": symbol,
        "Signal": signal,
        "EventDate": event_date,
        "EventTime": "",  # EOD: optional
        "CandleTime": "",

        # At EOD, the "trigger" price equals the daily close.
        # Also write final day OHLC so scan_alerts CSV can be used for simulations.
        "TriggerPrice": close,
        "DayOpen_AtAlert": o,
        "DayHigh_AtAlert": h,
        "DayLow_AtAlert": l,
        "DayClose_AtAlert": close,
        "DayOpen_Final": o,
        "DayHigh_Final": h,
        "DayLow_Final": l,
        "DayClose_Final": close,

        "TodayClose": close,
        "EMA20": ema20,
        "EMA20_H": ema20_h,
        "EMA20_L": ema20_l,
        "Ema20CrossLookbackTD": cross_stats.get("lookback_td"),
        "Ema20CrossCountTotal": cross_stats.get("count_total"),
        "Ema20CrossCountBull": cross_stats.get("count_bull"),
        "Ema20CrossCountBear": cross_stats.get("count_bear"),
        "Ema20CrossDaysSinceLast": cross_stats.get("days_since_last_cross"),
        "Ema20CrossDensity": cross_stats.get("cross_density"),

        "LatestCrossDate": cross_date,
        "LatestCrossDirection": cross_dir,

        "PrimaryWindowDaysUsed": d1,
        f"WindowHigh_{d1}D_preCross": wh1,
        f"WindowLow_{d1}D_preCross": wl1,
        f"BreakPct_{d1}D": break_pct1,
        "EmaDistance": ema_dist,
    }
    if secondary_enabled and d2 is not None and wh2 is not None and wl2 is not None:
        alert.update({
            "SecondaryWindowDaysUsed": d2,
            f"WindowHigh_{d2}D_preCross": wh2,
            f"WindowLow_{d2}D_preCross": wl2,
            f"BreakPct_{d2}D": break_pct2,
        })

    # write to ledger
    inserted = insert_alert_log(conn, alert, source="EOD")
    if inserted:
        set_armed(conn, symbol, 0)
        set_alert_info(conn, symbol, event_date, signal)

    return alert

def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Step 3: EOD scan from SQLite daily bars")
    p.add_argument("--asof", dest="asof", default="", help="As-of date YYYY-MM-DD (default: today)")
    p.add_argument("--discord", dest="discord", default="auto", choices=["auto","on","off"], help="Discord sending: auto=use config, on/off override")
    args = p.parse_args()

    run_date = args.asof.strip() or _today_str_local()
    discord_override = args.discord
    # IMPORTANT: honor --asof (used for repairing missing prior-session cross files)
    asof = run_date

    # Discord override behavior
    discord_enabled = bool(getattr(CFG, "DISCORD_ENABLED", False)) and bool(getattr(CFG, "DISCORD_WEBHOOK_URL", ""))
    if discord_override == "on":
        discord_enabled = True
    elif discord_override == "off":
        discord_enabled = False

    # EOD startup banner (console + optional Discord)
    primary_days = int(getattr(CFG, "WINDOW_DAYS_PRIMARY", 35))
    secondary_enabled = bool(getattr(CFG, "ENABLE_SECONDARY_WINDOW", True))
    secondary_days = int(getattr(CFG, "WINDOW_DAYS_SECONDARY", 21)) if secondary_enabled else None
    start_banner = "\n".join([
        f"🟦 EMA20 EOD Scan START ({'PROD'})",
        "",
        f"As-of: {asof}",
        f"EMA period: {getattr(CFG, 'EMA_PERIOD', 20)} | Cross lookback: {getattr(CFG, 'CROSS_LOOKBACK_DAYS', 30)} trading days",
        f"Primary window: {primary_days}D" + (f" | Secondary window: {secondary_days}D" if secondary_enabled and secondary_days else " | Secondary window: OFF"),
        f"Allow alert on cross date: {getattr(CFG, 'ALLOW_ALERT_ON_CROSS_DATE', True)}",
        f"Rearm on re-entry: {getattr(CFG, 'REARM_ON_REENTRY', True)} ({getattr(CFG, 'REENTRY_MODE', 'strict')})",
    ])
    print("\n" + "=" * 80)
    print(start_banner)
    print("=" * 80 + "\n")
    if discord_enabled and getattr(CFG, "DISCORD_SEND_EOD_BANNERS", True):
        send_discord_message(CFG.DISCORD_WEBHOOK_URL, start_banner)

    conn = connect_db(CFG.DB_PATH)
    init_db(conn, wal_mode=bool(getattr(CFG, "SQLITE_WAL_MODE", True)))
    init_state_tables(conn)
    init_alerts_log(conn)

    symbols, symbols_path = load_universe_symbols()

    rows_all: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    cross_rows: List[Dict[str, Any]] = []

    for sym in symbols:
        res = evaluate_eod_for_symbol(conn, sym, asof_date=asof)
        if res is None:
            continue

        rows_all.append(res)

        # cross-eligible means we have LatestCrossDate populated
        if res.get("LatestCrossDate"):
            cross_rows.append({
                "Symbol": sym,
                "LatestCrossDate": res.get("LatestCrossDate"),
                "LatestCrossDirection": res.get("LatestCrossDirection"),
            })

        if res.get("Signal") in ("LONG", "SHORT"):
            alerts.append(res)


    # Outputs
    scan_all_path = os.path.join(CFG.OUTPUT_DIR, f"scan_all_{asof}.csv")
    eod_alerts_path = os.path.join(CFG.OUTPUT_DIR, f"eod_scan_alerts_{asof}.csv")
    live_alerts_path = os.path.join(CFG.OUTPUT_DIR, f"live_alerts_{asof}.csv")
    # Backward-compat alias (some older tooling expects scan_alerts_*.csv to be the EOD scan file)
    scan_alerts_path = eod_alerts_path
    cross_path = os.path.join(CFG.SYMBOLS_DIR, f"ema20_cross_{asof}.csv")

    # 1) Full scan universe (all rows)
    if rows_all:
        pd.DataFrame(rows_all).to_csv(scan_all_path, index=False)
    else:
        pd.DataFrame([{"Symbol": "", "EventDate": asof, "Note": "No rows"}]).to_csv(scan_all_path, index=False)

    # 2) EOD scan alerts (daily-close breakout signals)
    def _norm_eod_row(a: Dict[str, Any]) -> Dict[str, Any]:
        d1 = int(a.get("PrimaryWindowDaysUsed") or CFG.WINDOW_DAYS_PRIMARY)
        d2 = a.get("SecondaryWindowDaysUsed")
        d2 = int(d2) if d2 not in (None, "", 0) else (int(CFG.WINDOW_DAYS_SECONDARY) if bool(CFG.ENABLE_SECONDARY_WINDOW) else None)

        wh1 = a.get(f"WindowHigh_{d1}D_preCross")
        wl1 = a.get(f"WindowLow_{d1}D_preCross")
        wh2 = a.get(f"WindowHigh_{d2}D_preCross") if d2 else None
        wl2 = a.get(f"WindowLow_{d2}D_preCross") if d2 else None

        bp1 = a.get(f"BreakPct_{d1}D")
        bp2 = a.get(f"BreakPct_{d2}D") if d2 else None

        # EOD candle time = market close candle timestamp in Chicago time (used for display; not system time)
        candle_time = f"{a.get('EventDate')} 15:00:00 CST"

        # For EOD scan alerts, we want the daily candle OHLC. Earlier versions of
        # the alert dict used Day*_Final fields; support both to avoid NULL/blank
        # exports.
        _o = a.get("Open") if a.get("Open") is not None else a.get("DayOpen_Final")
        _h = a.get("High") if a.get("High") is not None else a.get("DayHigh_Final")
        _l = a.get("Low") if a.get("Low") is not None else a.get("DayLow_Final")
        _c = a.get("Close") if a.get("Close") is not None else a.get("DayClose_Final")

        return {
            "Symbol": a.get("Symbol"),
            "Signal": a.get("Signal"),
            "EventDate": a.get("EventDate"),
            "CandleTime": candle_time,
            "EventTime": datetime.now().strftime("%H:%M:%S"),
            # Daily EOD scan: candle OHLC is the daily bar
            "CandleOpen": _to_float(_o),
            "CandleHigh": _to_float(_h),
            "CandleLow": _to_float(_l),
            "CandleClose": _to_float(_c),
            # EOD: AtAlert == Final == Candle (kept for schema parity with live alerts)
            "DayOpen_AtAlert": _to_float(_o),
            "DayHigh_AtAlert": _to_float(_h),
            "DayLow_AtAlert": _to_float(_l),
            "DayClose_AtAlert": _to_float(_c),
            "DayOpen_Final": _to_float(_o),
            "DayHigh_Final": _to_float(_h),
            "DayLow_Final": _to_float(_l),
            "DayClose_Final": _to_float(_c),
            "Close": _to_float(_c),
            "EMA20": _to_float(a.get("EMA20")),
            "EMA20_H": _to_float(a.get("EMA20_H")),
            "EMA20_L": _to_float(a.get("EMA20_L")),
            "PrimaryWindowDays": d1,
            f"WindowHigh_{d1}D_preCross": _to_float(wh1),
            f"WindowLow_{d1}D_preCross": _to_float(wl1),
            "SecondaryWindowDays": d2,
            (f"WindowHigh_{d2}D_preCross" if d2 else "WindowHigh_Secondary_preCross"): _to_float(wh2),
            (f"WindowLow_{d2}D_preCross" if d2 else "WindowLow_Secondary_preCross"): _to_float(wl2),
            (f"BreakPct_{d1}D" if d1 else "BreakPct_Primary"): _to_float(bp1),
            (f"BreakPct_{d2}D" if d2 else "BreakPct_Secondary"): _to_float(bp2),
            "EmaDistance": _to_float(a.get("EmaDistance")),
            "LatestCrossDate": a.get("LatestCrossDate"),
            "LatestCrossDirection": a.get("LatestCrossDirection"),
            "Ema20CrossLookbackTD": a.get("Ema20CrossLookbackTD"),
            "Ema20CrossCountTotal": a.get("Ema20CrossCountTotal"),
            "Ema20CrossCountBull": a.get("Ema20CrossCountBull"),
            "Ema20CrossCountBear": a.get("Ema20CrossCountBear"),
            "Ema20CrossDaysSinceLast": a.get("Ema20CrossDaysSinceLast"),
            "Ema20CrossDensity": a.get("Ema20CrossDensity"),
        }

    eod_alerts_df = pd.DataFrame([_norm_eod_row(a) for a in alerts]) if alerts else pd.DataFrame([])
    # Preserve existing EOD scan file if empty (safety against accidental overwrites)
    if (not eod_alerts_df.empty) or (not getattr(CFG, "PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY", True)) or (not os.path.exists(eod_alerts_path)):
        eod_alerts_df.to_csv(eod_alerts_path, index=False)

    # Persist EOD scan alerts into a dedicated DB (separate from LIVE ledger)
    try:
        os.makedirs(os.path.dirname(CFG.EOD_DB_PATH), exist_ok=True)
        eod_conn = connect_db(CFG.EOD_DB_PATH)
        init_eod_scan_alerts(eod_conn)
        for _, r in (eod_alerts_df.iterrows() if not eod_alerts_df.empty else []):
            insert_eod_scan_alert(eod_conn, {
                "symbol": r.get("Symbol"),
                "event_date": r.get("EventDate"),
                "event_time": r.get("EventTime"),
                "signal": r.get("Signal"),
                "cross_date": r.get("LatestCrossDate"),
                "cross_direction": r.get("LatestCrossDirection"),
                # Daily candle OHLC for the event day
                "open": _to_float(r.get("CandleOpen")),
                "high": _to_float(r.get("CandleHigh")),
                "low": _to_float(r.get("CandleLow")),
                # EOD scan exports use CandleClose for the daily close.
                "close": _to_float(r.get("CandleClose")),
                "ema20": _to_float(r.get("EMA20")),
                "ema20_h": _to_float(r.get("EMA20_H")),
                "ema20_l": _to_float(r.get("EMA20_L")),
                "candle_time": r.get("CandleTime"),
                "window_days_primary": int(r.get("PrimaryWindowDays") or 0) or None,
                "window_high_primary": _to_float(r.get(f"WindowHigh_{int(r.get('PrimaryWindowDays') or 0)}D_preCross")) if r.get("PrimaryWindowDays") else None,
                "window_low_primary": _to_float(r.get(f"WindowLow_{int(r.get('PrimaryWindowDays') or 0)}D_preCross")) if r.get("PrimaryWindowDays") else None,
                "window_days_secondary": int(r.get("SecondaryWindowDays")) if str(r.get("SecondaryWindowDays") or "").strip() else None,
                "window_high_secondary": _to_float(r.get(f"WindowHigh_{int(r.get('SecondaryWindowDays') or 0)}D_preCross")) if str(r.get("SecondaryWindowDays") or "").strip() else None,
                "window_low_secondary": _to_float(r.get(f"WindowLow_{int(r.get('SecondaryWindowDays') or 0)}D_preCross")) if str(r.get("SecondaryWindowDays") or "").strip() else None,
                "break_pct_primary": _to_float(r.get(f"BreakPct_{int(r.get('PrimaryWindowDays') or 0)}D")) if r.get("PrimaryWindowDays") else None,
                "break_pct_secondary": _to_float(r.get(f"BreakPct_{int(r.get('SecondaryWindowDays') or 0)}D")) if str(r.get("SecondaryWindowDays") or "").strip() else None,
                "ema_dist": _to_float(r.get("EmaDistance")),
                "ema20_cross_lookback_td": r.get("Ema20CrossLookbackTD"),
                "ema20_cross_count_total": r.get("Ema20CrossCountTotal"),
                "ema20_cross_count_bull": r.get("Ema20CrossCountBull"),
                "ema20_cross_count_bear": r.get("Ema20CrossCountBear"),
                "ema20_cross_days_since_last": r.get("Ema20CrossDaysSinceLast"),
                "ema20_cross_density": r.get("Ema20CrossDensity"),
            })
        eod_conn.close()
    except Exception as e:
        print(f"[WARN] Failed to persist EOD scan alerts to EOD DB: {e}")

    # 3) LIVE alerts ledger export for the day (intraday triggers)
    # Finalize day OHLC in ledger (fills Day*_Final using daily_bars)
    try:
        finalize_alerts_day_ohlc(conn, asof)
    except Exception as e:
        print(f"[WARN] finalize_alerts_day_ohlc failed: {e}")

    ledger_alerts_df = read_alerts_for_date(conn, asof)
    if (not ledger_alerts_df.empty) or (not getattr(CFG, "PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY", True)) or (not os.path.exists(live_alerts_path)):
        ledger_alerts_df.to_csv(live_alerts_path, index=False)

    if getattr(CFG, "SAVE_EMA20_CROSS_SYMBOLS", True):
        pd.DataFrame(cross_rows).drop_duplicates(subset=["Symbol"]).to_csv(cross_path, index=False)

    # Console dashboard
    print("\n" + "=" * 80)
    print(f"EOD SCAN COMPLETE | {asof}")
    print("-" * 80)
    print(f"Universe symbols: {len(symbols)} (from {os.path.basename(symbols_path)})")
    print(f"Cross-eligible: {len(cross_rows)}")
    print(f"EOD scan alerts: {len(eod_alerts_df)}")
    print(f"Live ledger alerts: {len(ledger_alerts_df)}")
    print(f"Saved scan_all:  {scan_all_path}")
    print(f"Saved alerts:    {scan_alerts_path}")
    if getattr(CFG, "SAVE_EMA20_CROSS_SYMBOLS", True):
        print(f"Saved cross file:{cross_path}")
    print("=" * 80 + "\n")

    # Optional Discord summary / completion banner
    if getattr(CFG, "DISCORD_ENABLED", False) and getattr(CFG, "DISCORD_WEBHOOK_URL", "") and getattr(CFG, "DISCORD_SEND_EOD_SUMMARY", True):
        msg = f"EMA20 EOD Scan {asof} | Universe {len(symbols)} | Cross {len(cross_rows)} | EOD Alerts {len(eod_alerts_df)} | Live Alerts {len(ledger_alerts_df)} | Primary {primary_days}D" + (f" | Secondary {secondary_days}D" if secondary_enabled and secondary_days else " | Secondary OFF")
        send_discord_message(CFG.DISCORD_WEBHOOK_URL, msg)

    # Optional: compact alerts table (top N rows) for quick glance in Discord
    if (
        getattr(CFG, "DISCORD_ENABLED", False)
        and getattr(CFG, "DISCORD_WEBHOOK_URL", "")
        and getattr(CFG, "DISCORD_SEND_EOD_ALERTS_TABLE", True)
    ):
        try:
            alerts_df = ledger_alerts_df if not ledger_alerts_df.empty else pd.DataFrame([])
            table = format_alerts_table(alerts_df, max_rows=getattr(CFG, "DISCORD_MAX_ALERTS", 10))
            send_discord_message(CFG.DISCORD_WEBHOOK_URL, table)
        except Exception as e:
            print(f"[WARN] Discord alerts table failed: {e}")

    if (
        getattr(CFG, "DISCORD_ENABLED", False)
        and getattr(CFG, "DISCORD_WEBHOOK_URL", "")
        and getattr(CFG, "DISCORD_SEND_EOD_BANNERS", True)
    ):
        done_banner = "\n".join([
            f"🟥 EMA20 EOD Scan DONE ({'PROD'})",
            "",
            f"As-of: {asof}",
            f"Universe: {len(symbols)} | Cross eligible: {len(cross_rows)} | Alerts: {len(alerts)}",
            f"Primary window: {primary_days}D" + (f" | Secondary window: {secondary_days}D" if secondary_enabled and secondary_days else " | Secondary window: OFF"),
            "",
            f"scan_all: {os.path.basename(scan_all_path)}",
            f"alerts:   {os.path.basename(scan_alerts_path)}",
            f"cross:    {os.path.basename(cross_path)}" if getattr(CFG, "SAVE_EMA20_CROSS_SYMBOLS", True) else "cross:    (disabled)",
        ])
        send_discord_message(CFG.DISCORD_WEBHOOK_URL, done_banner)

        # Optional: upload the alerts CSV as an attachment (recommended for full detail)
        if getattr(CFG, "DISCORD_SEND_EOD_ALERTS_CSV", False):
            only_if_alerts = getattr(CFG, "DISCORD_UPLOAD_CSV_ONLY_IF_ALERTS", True)
            if (not only_if_alerts) or (len(alerts) > 0):
                try:
                    # Attach BOTH: (1) EOD daily-close breakout signals and (2) LIVE intraday triggers ledger
                    send_discord_file(
                        CFG.DISCORD_WEBHOOK_URL,
                        eod_alerts_path,
                        content=f"📎 EOD Scan Alerts CSV attached ({asof})",
                    )
                    send_discord_file(
                        CFG.DISCORD_WEBHOOK_URL,
                        live_alerts_path,
                        content=f"📎 LIVE Alerts CSV attached ({asof})",
                    )
                except Exception as e:
                    print(f"[WARN] Discord CSV upload failed: {e}")

    conn.close()

if __name__ == "__main__":
    main()
