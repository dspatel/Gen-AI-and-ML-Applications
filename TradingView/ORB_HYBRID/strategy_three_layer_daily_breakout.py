from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DEFAULT_SYMBOLS_FILE = Path(__file__).resolve().parent / "universes" / "focus_symbols_v1.txt"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "alpaca_daily_cache.sqlite"
DEFAULT_REPORT_SUMMARY = Path(__file__).resolve().parent / "reports" / "three_layer_daily_summary.csv"
DEFAULT_REPORT_TRADES = Path(__file__).resolve().parent / "reports" / "three_layer_daily_trades.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Three-layer strategy test: monthly EMA20 bias + HMM regime + daily EMA20 cross breakout."
    )
    p.add_argument("--symbols", default="")
    p.add_argument("--symbols-file", default=str(DEFAULT_SYMBOLS_FILE))
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--start", default="2018-01-01T00:00:00Z")
    p.add_argument("--end", default="")
    p.add_argument("--alpaca-key", default="")
    p.add_argument("--alpaca-secret", default="")
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex")
    p.add_argument("--alpaca-base-url", default="https://data.alpaca.markets")
    p.add_argument("--adjustment", choices=["raw", "split", "dividend", "all"], default="split")
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--breakout-windows", default="7,21,35")
    p.add_argument("--hold-days", default="5,10")
    p.add_argument("--range-mode", choices=["rolling", "anchored"], default="anchored")
    p.add_argument("--setup-max-days", type=int, default=15)
    p.add_argument("--hmm-states", type=int, default=3)
    p.add_argument("--hmm-min-train-rows", type=int, default=180)
    p.add_argument("--hmm-random-state", type=int, default=42)
    p.add_argument("--timeout-sec", type=int, default=40)
    p.add_argument("--save-summary-csv", default=str(DEFAULT_REPORT_SUMMARY))
    p.add_argument("--save-trades-csv", default=str(DEFAULT_REPORT_TRADES))
    return p.parse_args()


def _parse_int_csv(s: str) -> list[int]:
    out = [int(x.strip()) for x in s.split(",") if x.strip()]
    out = sorted(dict.fromkeys([x for x in out if x > 0]))
    if not out:
        raise ValueError("At least one positive integer value is required.")
    return out


def _load_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols.strip():
        vals = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return sorted(dict.fromkeys(vals))

    p = Path(args.symbols_file)
    if not p.exists():
        raise FileNotFoundError(f"Symbols file not found: {p}")
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        x = line.strip()
        if not x or x.startswith("#"):
            continue
        sym = x.split(":")[0].strip().upper()
        if sym:
            out.append(sym)
    return sorted(dict.fromkeys(out))


def _ensure_daily_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_bars (
            source_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            PRIMARY KEY (source_key, symbol, ts_utc)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_bars_lookup
        ON daily_bars (source_key, symbol, ts_utc)
        """
    )
    conn.commit()


def _fetch_daily_symbol(
    *,
    symbol: str,
    key: str,
    secret: str,
    feed: str,
    base_url: str,
    start: str,
    end: str,
    adjustment: str,
    timeout_sec: int,
) -> pd.DataFrame:
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    url = base_url.rstrip("/") + "/v2/stocks/bars"
    page_token = ""
    rows: list[dict] = []
    while True:
        params = {
            "symbols": symbol,
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": 10000,
            "adjustment": adjustment,
            "feed": feed,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params, timeout=timeout_sec)
        if resp.status_code != 200:
            raise RuntimeError(f"{symbol}: Alpaca API error {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        bars = payload.get("bars", {}).get(symbol, []) or []
        rows.extend(bars)
        page_token = payload.get("next_page_token") or ""
        if not page_token:
            break

    if not rows:
        return pd.DataFrame(columns=["symbol", "ts_utc", "open", "high", "low", "close", "volume"])

    d = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "symbol": symbol,
            "ts_utc": pd.to_datetime(d["t"], utc=True).astype(str),
            "open": d["o"].astype(float),
            "high": d["h"].astype(float),
            "low": d["l"].astype(float),
            "close": d["c"].astype(float),
            "volume": d["v"].astype(float),
        }
    )
    out = out.sort_values("ts_utc").drop_duplicates(subset=["ts_utc"]).reset_index(drop=True)
    return out


def _upsert_daily(conn: sqlite3.Connection, source_key: str, bars: pd.DataFrame) -> int:
    if bars.empty:
        return 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    payload = [
        (
            source_key,
            str(r.symbol),
            str(r.ts_utc),
            float(r.open),
            float(r.high),
            float(r.low),
            float(r.close),
            float(r.volume),
            fetched_at,
        )
        for r in bars.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_bars
        (source_key, symbol, ts_utc, open, high, low, close, volume, fetched_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    conn.commit()
    return len(payload)


def _read_daily(conn: sqlite3.Connection, source_key: str, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    marks = ",".join(["?"] * len(symbols))
    q = f"""
        SELECT symbol, ts_utc, open, high, low, close, volume
        FROM daily_bars
        WHERE source_key = ? AND symbol IN ({marks})
        ORDER BY symbol, ts_utc
    """
    d = pd.read_sql_query(q, conn, params=[source_key] + symbols)
    if d.empty:
        return d
    d["ts_utc"] = pd.to_datetime(d["ts_utc"], utc=True, errors="coerce")
    d = d.dropna(subset=["ts_utc"]).copy()
    d["date"] = d["ts_utc"].dt.tz_convert("America/New_York").dt.tz_localize(None)
    return d.sort_values(["symbol", "date"]).reset_index(drop=True)


def _fit_hmm_states(
    df: pd.DataFrame,
    train_mask: pd.Series,
    n_states: int,
    min_train_rows: int,
    random_state: int,
) -> tuple[pd.Series, set[int], set[int]]:
    try:
        from hmmlearn.hmm import GaussianHMM  # type: ignore
        from sklearn.preprocessing import StandardScaler
    except Exception:
        return pd.Series(index=df.index, dtype=float), set(), set()

    z = df.copy()
    z["ret1"] = z["close"].pct_change()
    z["ret5"] = z["close"].pct_change(5)
    z["vol5"] = z["ret1"].rolling(5, min_periods=5).std()
    z["fwd5"] = z["close"].shift(-5) / z["close"] - 1.0
    feat_cols = ["ret1", "ret5", "vol5"]
    valid = z[feat_cols].notna().all(axis=1)
    tr = valid & train_mask
    if int(tr.sum()) < int(min_train_rows):
        return pd.Series(index=df.index, dtype=float), set(), set()

    x_train = z.loc[tr, feat_cols].to_numpy(dtype=float)
    scaler = StandardScaler()
    x_train_s = scaler.fit_transform(x_train)
    model = GaussianHMM(
        n_components=max(int(n_states), 2),
        covariance_type="diag",
        n_iter=250,
        random_state=int(random_state),
    )
    try:
        model.fit(x_train_s)
    except Exception:
        return pd.Series(index=df.index, dtype=float), set(), set()

    state_series = pd.Series(index=df.index, dtype=float)
    tr_states = model.predict(x_train_s)
    state_series.loc[z.index[tr]] = tr_states

    # Derive positive/negative regime states from train forward 5d expectancy.
    train_map = (
        pd.DataFrame({"state": tr_states, "fwd5": z.loc[tr, "fwd5"].to_numpy()})
        .dropna(subset=["fwd5"])
        .groupby("state", as_index=False)
        .agg(mean_fwd5=("fwd5", "mean"))
    )
    pos_states = set(train_map[train_map["mean_fwd5"] > 0.0]["state"].astype(int).tolist())
    neg_states = set(train_map[train_map["mean_fwd5"] < 0.0]["state"].astype(int).tolist())

    # Online decode for test to avoid using future observations.
    valid_idx = z.index[valid].tolist()
    if valid_idx:
        x_all = scaler.transform(z.loc[valid, feat_cols].to_numpy(dtype=float))
        pos_map = {idx: i for i, idx in enumerate(valid_idx)}
        for idx in z.index[valid & (~train_mask)]:
            i = pos_map[idx]
            if i < 0:
                continue
            try:
                state_series.loc[idx] = int(model.predict(x_all[: i + 1])[-1])
            except Exception:
                state_series.loc[idx] = np.nan

    return state_series, pos_states, neg_states


def _add_monthly_bias(df: pd.DataFrame) -> pd.DataFrame:
    z = df.copy()
    z["month"] = z["date"].dt.to_period("M")
    m = z.groupby("month", as_index=False).agg(month_close=("close", "last"))
    m["ema20_m"] = m["month_close"].ewm(span=20, adjust=False).mean()
    m["bull_m"] = (m["month_close"] > m["ema20_m"]).astype(int)
    m["month_for_next"] = m["month"] + 1
    z = z.merge(m[["month_for_next", "bull_m"]], left_on="month", right_on="month_for_next", how="left")
    z["monthly_bull"] = z["bull_m"].fillna(0).astype(int)
    return z.drop(columns=["month_for_next", "bull_m"])


def _collect_trades_for_symbol(
    df: pd.DataFrame,
    symbol: str,
    breakout_windows: list[int],
    hold_days_list: list[int],
    range_mode: str,
    setup_max_days: int,
    train_ratio: float,
    hmm_states: int,
    hmm_min_train_rows: int,
    hmm_random_state: int,
) -> pd.DataFrame:
    z = df[df["symbol"] == symbol].sort_values("date").reset_index(drop=True).copy()
    if len(z) < 260:
        return pd.DataFrame()

    split_idx = int(len(z) * float(train_ratio))
    split_idx = max(120, min(split_idx, len(z) - 30))
    train_mask = pd.Series([True] * split_idx + [False] * (len(z) - split_idx), index=z.index)

    z["ema20_d"] = z["close"].ewm(span=20, adjust=False).mean()
    z["prev_close"] = z["close"].shift(1)
    z["prev_ema20_d"] = z["ema20_d"].shift(1)
    z["cross_up_d"] = (z["prev_close"] <= z["prev_ema20_d"]) & (z["close"] > z["ema20_d"])
    z["cross_down_d"] = (z["prev_close"] >= z["prev_ema20_d"]) & (z["close"] < z["ema20_d"])
    z = _add_monthly_bias(z)

    hmm_state, pos_states, neg_states = _fit_hmm_states(
        z,
        train_mask=train_mask,
        n_states=hmm_states,
        min_train_rows=hmm_min_train_rows,
        random_state=hmm_random_state,
    )
    z["hmm_state"] = hmm_state

    max_hold = max(hold_days_list)
    rows: list[dict] = []
    for win in breakout_windows:
        zh = z.copy()
        zh[f"high_{win}"] = zh["high"].shift(1).rolling(win, min_periods=win).max()
        zh[f"low_{win}"] = zh["low"].shift(1).rolling(win, min_periods=win).min()

        up_expiry = -1
        down_expiry = -1
        anchor_high = np.nan
        anchor_low = np.nan
        regime = ""
        signal_used = False
        for i in range(len(zh)):
            if i + max_hold >= len(zh):
                break

            cross_up = bool(zh.at[i, "cross_up_d"])
            cross_down = bool(zh.at[i, "cross_down_d"])

            if range_mode == "rolling":
                if cross_up:
                    up_expiry = i + int(setup_max_days)
                if cross_down:
                    down_expiry = i + int(setup_max_days)
            else:
                if cross_up:
                    hi = zh.at[i, f"high_{win}"]
                    lo = zh.at[i, f"low_{win}"]
                    if not pd.isna(hi) and not pd.isna(lo):
                        anchor_high = float(hi)
                        anchor_low = float(lo)
                        regime = "up"
                        signal_used = False
                if cross_down:
                    hi = zh.at[i, f"high_{win}"]
                    lo = zh.at[i, f"low_{win}"]
                    if not pd.isna(hi) and not pd.isna(lo):
                        anchor_high = float(hi)
                        anchor_low = float(lo)
                        regime = "down"
                        signal_used = False

            is_test = bool(~train_mask.iloc[i])
            if not is_test:
                continue

            close_i = float(zh.at[i, "close"])
            if range_mode == "rolling":
                hi = zh.at[i, f"high_{win}"]
                lo = zh.at[i, f"low_{win}"]
            else:
                hi = anchor_high
                lo = anchor_low
            if pd.isna(hi) or pd.isna(lo):
                continue

            monthly_bull = int(zh.at[i, "monthly_bull"])
            st = zh.at[i, "hmm_state"]
            hmm_long_ok = (not pd.isna(st)) and (int(st) in pos_states)
            hmm_short_ok = (not pd.isna(st)) and (int(st) in neg_states)

            # Long candidate: cross-up setup then breakout above rolling high.
            if range_mode == "rolling":
                long_candidate = (i <= up_expiry) and (close_i > float(hi))
            else:
                long_candidate = (regime == "up") and (not signal_used) and (close_i > float(hi))
            if long_candidate:
                mode_ok = {
                    "breakout_only": True,
                    "monthly_only": monthly_bull == 1,
                    "hmm_only": bool(hmm_long_ok),
                    "full_three_layer": (monthly_bull == 1) and bool(hmm_long_ok),
                }
                for hold in hold_days_list:
                    ret = float(zh.at[i + hold, "close"] / close_i - 1.0)
                    for mode, ok in mode_ok.items():
                        if ok:
                            rows.append(
                                {
                                    "symbol": symbol,
                                    "date": zh.at[i, "date"],
                                    "side": "long",
                                    "mode": mode,
                                    "breakout_window": int(win),
                                    "hold_days": int(hold),
                                    "ret": ret,
                                }
                            )
                if range_mode == "rolling":
                    up_expiry = -1
                else:
                    signal_used = True

            # Short candidate: cross-down setup then breakout below rolling low.
            if range_mode == "rolling":
                short_candidate = (i <= down_expiry) and (close_i < float(lo))
            else:
                short_candidate = (regime == "down") and (not signal_used) and (close_i < float(lo))
            if short_candidate:
                mode_ok = {
                    "breakout_only": True,
                    "monthly_only": monthly_bull == 0,
                    "hmm_only": bool(hmm_short_ok),
                    "full_three_layer": (monthly_bull == 0) and bool(hmm_short_ok),
                }
                for hold in hold_days_list:
                    ret = float(close_i / zh.at[i + hold, "close"] - 1.0)
                    for mode, ok in mode_ok.items():
                        if ok:
                            rows.append(
                                {
                                    "symbol": symbol,
                                    "date": zh.at[i, "date"],
                                    "side": "short",
                                    "mode": mode,
                                    "breakout_window": int(win),
                                    "hold_days": int(hold),
                                    "ret": ret,
                                }
                            )
                if range_mode == "rolling":
                    down_expiry = -1
                else:
                    signal_used = True

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out["win"] = (out["ret"] > 0.0).astype(int)
    return out


def _summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()

    g = (
        trades.groupby(["mode", "side", "breakout_window", "hold_days"], as_index=False)
        .agg(
            trades=("ret", "size"),
            hit_rate=("win", "mean"),
            avg_ret=("ret", "mean"),
            median_ret=("ret", "median"),
            total_ret=("ret", "sum"),
            symbols_covered=("symbol", "nunique"),
        )
        .sort_values(["mode", "side", "breakout_window", "hold_days"])
        .reset_index(drop=True)
    )
    g["hit_rate_pct"] = 100.0 * g["hit_rate"]
    g["avg_ret_pct"] = 100.0 * g["avg_ret"]
    g["median_ret_pct"] = 100.0 * g["median_ret"]
    g["total_ret_pct"] = 100.0 * g["total_ret"]
    return g


def main() -> None:
    args = _parse_args()
    symbols = _load_symbols(args)
    breakout_windows = _parse_int_csv(args.breakout_windows)
    hold_days_list = _parse_int_csv(args.hold_days)

    key = (args.alpaca_key or os.getenv("APCA_API_KEY_ID", "")).strip()
    secret = (args.alpaca_secret or os.getenv("APCA_API_SECRET_KEY", "")).strip()
    if not key or not secret:
        raise RuntimeError("Alpaca credentials missing. Set APCA_API_KEY_ID/APCA_API_SECRET_KEY or pass flags.")

    end = args.end.strip() or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_key = f"alpaca:{args.alpaca_feed}:{args.adjustment}:1Day"
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    _ensure_daily_schema(conn)

    if args.refresh:
        print("=== Refresh Daily Bars ===")
        print(f"symbols={len(symbols)} source_key={source_key}")
        print(f"start={args.start} end={end}")
        for sym in symbols:
            bars = _fetch_daily_symbol(
                symbol=sym,
                key=key,
                secret=secret,
                feed=args.alpaca_feed,
                base_url=args.alpaca_base_url,
                start=args.start,
                end=end,
                adjustment=args.adjustment,
                timeout_sec=args.timeout_sec,
            )
            n = _upsert_daily(conn, source_key=source_key, bars=bars)
            if bars.empty:
                print(f"{sym}: 0 rows")
            else:
                print(f"{sym}: {n} rows | {bars['ts_utc'].min()} -> {bars['ts_utc'].max()}")

    daily = _read_daily(conn, source_key=source_key, symbols=symbols)
    conn.close()
    if daily.empty:
        raise RuntimeError("No daily bars found in cache for requested source/symbols.")

    print("\n=== Strategy Setup ===")
    print(f"symbols_requested={len(symbols)} symbols_in_data={daily['symbol'].nunique()}")
    print(f"date_range={daily['date'].min().date()} to {daily['date'].max().date()}")
    print(f"breakout_windows={breakout_windows} hold_days={hold_days_list} range_mode={args.range_mode}")
    print(
        f"train_ratio={args.train_ratio} setup_max_days={args.setup_max_days} "
        f"hmm_states={args.hmm_states} hmm_min_train_rows={args.hmm_min_train_rows}"
    )

    trades_all: list[pd.DataFrame] = []
    for j, sym in enumerate(sorted(daily["symbol"].unique()), start=1):
        tr = _collect_trades_for_symbol(
            daily,
            symbol=sym,
            breakout_windows=breakout_windows,
            hold_days_list=hold_days_list,
            range_mode=args.range_mode,
            setup_max_days=args.setup_max_days,
            train_ratio=args.train_ratio,
            hmm_states=args.hmm_states,
            hmm_min_train_rows=args.hmm_min_train_rows,
            hmm_random_state=args.hmm_random_state + j,
        )
        if tr.empty:
            print(f"{sym}: no eligible test trades")
            continue
        trades_all.append(tr)
        print(f"{sym}: trades={len(tr)}")

    trades = pd.concat(trades_all, ignore_index=True) if trades_all else pd.DataFrame()
    if trades.empty:
        raise RuntimeError("No test trades generated. Try lower thresholds or longer data span.")

    summary = _summarize(trades)
    print("\n=== Summary (Out-of-Sample Test Segment) ===")
    show_cols = [
        "mode",
        "side",
        "breakout_window",
        "hold_days",
        "trades",
        "symbols_covered",
        "hit_rate_pct",
        "avg_ret_pct",
        "median_ret_pct",
        "total_ret_pct",
    ]
    print(summary[show_cols].to_string(index=False))

    summary_path = Path(args.save_summary_csv)
    trades_path = Path(args.save_trades_csv)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    trades_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False)
    trades.to_csv(trades_path, index=False)
    print(f"\nSaved summary CSV: {summary_path}")
    print(f"Saved trades CSV: {trades_path}")


if __name__ == "__main__":
    main()
