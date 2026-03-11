from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


ORB_BASE_STRATEGY = "TF15_EMA20_TRAIL_LIMIT1_LONG_CUTOFF_NONE"
R6_BASE_VARIANT = "R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY"
LOCK_THRESHOLD_R = 0.25
TRAIN_TEST_FOLDS = [
    ([2023], 2024),
    ([2023, 2024], 2025),
    ([2023, 2024, 2025], 2026),
]


@dataclass(frozen=True)
class Predicate:
    name: str
    fn: Callable[[pd.DataFrame], pd.Series]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward regime router evaluation for profit-lock policy (0R vs 0.25R).")
    p.add_argument("--orb-db", default="./orb_research.db")
    p.add_argument("--r6-db", default="./artifacts/orb_r6/orb_core.sqlite")
    p.add_argument("--output-csv", default="./artifacts/reports/regime_lock_router_fold_results.csv")
    p.add_argument("--output-json", default="./artifacts/reports/regime_lock_router_summary.json")
    p.add_argument("--output-trades-csv", default="./artifacts/reports/regime_lock_router_trade_details.csv")
    return p.parse_args()


def _metrics_from_vectors(r: np.ndarray, ret: np.ndarray) -> dict:
    if len(r) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "pf": 0.0,
            "sum_r": 0.0,
            "comp_ret_pct": 0.0,
        }
    gp = float(r[r > 0].sum())
    gl = float(-r[r < 0].sum())
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    comp = float((1.0 + ret).prod() - 1.0)
    return {
        "trades": int(len(r)),
        "win_rate": float((r > 0).mean()),
        "avg_r": float(r.mean()),
        "pf": float(pf),
        "sum_r": float(r.sum()),
        "comp_ret_pct": float(comp * 100.0),
    }


def _policy_metrics(df: pd.DataFrame, use_lock: np.ndarray) -> dict:
    r = np.where(use_lock, df["r_lock"].to_numpy(dtype=float), df["r_no_lock"].to_numpy(dtype=float))
    ret = np.where(use_lock, df["ret_lock"].to_numpy(dtype=float), df["ret_no_lock"].to_numpy(dtype=float))
    return _metrics_from_vectors(r, ret)


def _objective(m: dict) -> tuple[float, float, float]:
    return (float(m["comp_ret_pct"]), float(m["avg_r"]), float(m["pf"]))


def _resample_15m_from_5m(df_5m: pd.DataFrame) -> pd.DataFrame:
    out_parts: list[pd.DataFrame] = []
    for symbol, gsym in df_5m.groupby("symbol", sort=False):
        for session_date, gday in gsym.groupby("session_date", sort=False):
            r = (
                gday.set_index("ts_utc")
                .resample("15min")
                .agg({"o": "first", "h": "max", "l": "min", "c": "last", "volume": "sum"})
                .dropna()
                .reset_index()
            )
            if r.empty:
                continue
            r["symbol"] = symbol
            r["session_date"] = session_date
            r["ts_ct"] = r["ts_utc"].dt.tz_convert("America/Chicago")
            out_parts.append(r[["symbol", "session_date", "ts_utc", "ts_ct", "o", "h", "l", "c", "volume"]])
    if not out_parts:
        return pd.DataFrame(columns=["symbol", "session_date", "ts_utc", "ts_ct", "o", "h", "l", "c", "volume"])
    out = pd.concat(out_parts, ignore_index=True)
    out = out.sort_values(["symbol", "ts_utc"]).reset_index(drop=True)
    return out


def _build_or_features(df_5m: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for (symbol, session_date), g in df_5m.groupby(["symbol", "session_date"], sort=False):
        owin = g[(g["time_min_ct"] >= 8 * 60 + 30) & (g["time_min_ct"] < 9 * 60)].copy()
        if owin.empty:
            continue
        or_high = float(owin["h"].max())
        or_low = float(owin["l"].min())
        open_px = float(owin.iloc[0]["o"])
        if open_px <= 0:
            continue
        rows.append(
            {
                "symbol": symbol,
                "session_date": session_date,
                "or_high": or_high,
                "or_low": or_low,
                "or_width_pct": (or_high - or_low) / open_px,
            }
        )
    return pd.DataFrame(rows)


def _compute_be_hit_with_next_bar_arm(
    bars: pd.DataFrame, side: str, entry_price: float, risk: float, entry_utc: pd.Timestamp, exit_utc: pd.Timestamp
) -> int:
    if risk <= 0:
        return 0
    w = bars[(bars["ts_utc"] >= entry_utc) & (bars["ts_utc"] <= exit_utc)]
    if w.empty:
        return 0
    trigger = entry_price + LOCK_THRESHOLD_R * risk if side == "LONG" else entry_price - LOCK_THRESHOLD_R * risk
    armed = False
    for _, bar in w.iterrows():
        high = float(bar["h"])
        low = float(bar["l"])
        if not armed:
            if (side == "LONG" and high >= trigger) or (side == "SHORT" and low <= trigger):
                armed = True
            continue
        if (side == "LONG" and low <= entry_price) or (side == "SHORT" and high >= entry_price):
            return 1
    return 0


def _build_bar_arrays(bars_by_symbol: dict[str, pd.DataFrame]) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for sym, b in bars_by_symbol.items():
        s = b.sort_values("ts_utc")
        ts = (
            pd.to_datetime(s["ts_utc"], utc=True, errors="coerce")
            .dt.tz_localize(None)
            .astype("datetime64[ns]")
            .astype("int64")
            .to_numpy()
        )
        h = s["h"].astype(float).to_numpy()
        l = s["l"].astype(float).to_numpy()
        out[sym] = (ts, h, l)
    return out


def _compute_be_hit_fast(
    bar_arrays: tuple[np.ndarray, np.ndarray, np.ndarray],
    side: str,
    entry_price: float,
    risk: float,
    entry_utc: pd.Timestamp,
    exit_utc: pd.Timestamp,
) -> int:
    if risk <= 0:
        return 0
    ts, h, l = bar_arrays
    if ts.size == 0:
        return 0
    lo_idx = int(np.searchsorted(ts, entry_utc.value, side="left"))
    hi_idx = int(np.searchsorted(ts, exit_utc.value, side="right"))
    if hi_idx <= lo_idx:
        return 0

    hs = h[lo_idx:hi_idx]
    ls = l[lo_idx:hi_idx]
    trigger = entry_price + LOCK_THRESHOLD_R * risk if side == "LONG" else entry_price - LOCK_THRESHOLD_R * risk
    armed = False
    for i in range(len(hs)):
        high = float(hs[i])
        low = float(ls[i])
        if not armed:
            if (side == "LONG" and high >= trigger) or (side == "SHORT" and low <= trigger):
                armed = True
            continue
        if (side == "LONG" and low <= entry_price) or (side == "SHORT" and high >= entry_price):
            return 1
    return 0


def _load_orb_dataset(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        runs = pd.read_sql_query(
            """
            WITH ranked AS (
                SELECT symbol, run_id, started_at,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY started_at DESC) rn
                FROM strategy_runs
                WHERE mode='orb' AND status='completed'
                  AND start_date <= '2023-01-01' AND end_date >= '2026-02-23'
            )
            SELECT symbol, run_id
            FROM ranked
            WHERE rn=1
            ORDER BY symbol
            """,
            conn,
        )
        if runs.empty:
            return pd.DataFrame()
        run_ids = runs["run_id"].tolist()
        run_ph = ",".join(["?"] * len(run_ids))
        trades = pd.read_sql_query(
            f"""
            SELECT symbol, session_date, side, entry_ts, exit_ts, entry_price, risk, r_mult, ret_pct
            FROM trades
            WHERE run_id IN ({run_ph}) AND strategy_id=?
            ORDER BY symbol, session_date, entry_ts
            """,
            conn,
            params=[*run_ids, ORB_BASE_STRATEGY],
        )
        if trades.empty:
            return pd.DataFrame()

        symbols = sorted(trades["symbol"].dropna().unique().tolist())
        sym_ph = ",".join(["?"] * len(symbols))
        bars_5m = pd.read_sql_query(
            f"""
            SELECT symbol, ts, o, h, l, c, volume
            FROM bars_5m
            WHERE symbol IN ({sym_ph})
              AND ts >= '2023-01-01 08:30:00-0600'
              AND ts <= '2026-02-24 15:00:00-0600'
            ORDER BY symbol, ts
            """,
            conn,
            params=symbols,
        )
    finally:
        conn.close()

    if bars_5m.empty:
        return pd.DataFrame()

    bars_5m["ts_utc"] = pd.to_datetime(bars_5m["ts"], utc=True, errors="coerce")
    bars_5m = bars_5m.dropna(subset=["ts_utc"]).sort_values(["symbol", "ts_utc"]).reset_index(drop=True)
    bars_5m["ts_ct"] = bars_5m["ts_utc"].dt.tz_convert("America/Chicago")
    bars_5m["session_date"] = bars_5m["ts_ct"].dt.strftime("%Y-%m-%d")
    bars_5m["time_min_ct"] = bars_5m["ts_ct"].dt.hour * 60 + bars_5m["ts_ct"].dt.minute
    bars_5m["ema20"] = bars_5m.groupby("symbol", sort=False)["c"].transform(lambda s: s.ewm(span=20, adjust=False).mean())

    bars_15m = _resample_15m_from_5m(bars_5m)
    bars_by_symbol = {s: g.copy() for s, g in bars_15m.groupby("symbol", sort=False)}
    bar_arrays = _build_bar_arrays(bars_by_symbol)

    or_map = _build_or_features(bars_5m)
    or_map = or_map.set_index(["symbol", "session_date"]) if not or_map.empty else pd.DataFrame()

    b5_by_symbol = {s: g.copy() for s, g in bars_5m.groupby("symbol", sort=False)}
    ema_arrays: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for sym, b in b5_by_symbol.items():
        s = b.sort_values("ts_utc")
        ets = (
            pd.to_datetime(s["ts_utc"], utc=True, errors="coerce")
            .dt.tz_localize(None)
            .astype("datetime64[ns]")
            .astype("int64")
            .to_numpy()
        )
        ema_arrays[sym] = (ets, s["ema20"].astype(float).to_numpy())

    trades = trades.copy()
    trades["entry_utc"] = pd.to_datetime(trades["entry_ts"], utc=True, errors="coerce")
    trades["exit_utc"] = pd.to_datetime(trades["exit_ts"], utc=True, errors="coerce")
    trades = trades.dropna(subset=["entry_utc", "exit_utc"]).reset_index(drop=True)
    trades["entry_ct"] = trades["entry_utc"].dt.tz_convert("America/Chicago")
    trades["year"] = trades["entry_ct"].dt.year.astype(int)
    trades["entry_min_ct"] = trades["entry_ct"].dt.hour * 60 + trades["entry_ct"].dt.minute
    trades["side_short"] = (trades["side"].str.upper() == "SHORT").astype(int)
    trades["r_no_lock"] = pd.to_numeric(trades["r_mult"], errors="coerce").fillna(0.0)
    trades["ret_no_lock"] = pd.to_numeric(trades["ret_pct"], errors="coerce").fillna(0.0)
    trades["risk"] = pd.to_numeric(trades["risk"], errors="coerce").fillna(0.0)
    trades["entry_price"] = pd.to_numeric(trades["entry_price"], errors="coerce").fillna(0.0)

    or_width = []
    or_break_dist_r = []
    ema_gap_pct = []
    be_hits = []
    for row in trades.itertuples(index=False):
        key = (row.symbol, row.session_date)
        if isinstance(or_map, pd.DataFrame) and not or_map.empty and key in or_map.index:
            rr = or_map.loc[key]
            hi = float(rr["or_high"])
            lo = float(rr["or_low"])
            w = float(rr["or_width_pct"])
        else:
            hi = np.nan
            lo = np.nan
            w = np.nan
        or_width.append(w)
        if row.risk > 0 and np.isfinite(hi) and np.isfinite(lo):
            d = (row.entry_price - hi) / row.risk if row.side == "LONG" else (lo - row.entry_price) / row.risk
            or_break_dist_r.append(float(d))
        else:
            or_break_dist_r.append(np.nan)

        b_arr = bar_arrays.get(row.symbol)
        if b_arr is None:
            ema_gap_pct.append(np.nan)
            be_hits.append(0)
            continue

        # Entry EMA proxy from 5m series at/just before entry.
        earr = ema_arrays.get(row.symbol)
        if earr is None or earr[0].size == 0:
            ema_gap_pct.append(np.nan)
        else:
            ets, emv = earr
            j = int(np.searchsorted(ets, row.entry_utc.value, side="right") - 1)
            if j < 0:
                ema_gap_pct.append(np.nan)
            else:
                ema = float(emv[j])
                ema_gap_pct.append(abs(row.entry_price - ema) / row.entry_price if row.entry_price > 0 else np.nan)

        hit = _compute_be_hit_fast(
            bar_arrays=b_arr,
            side=str(row.side).upper(),
            entry_price=float(row.entry_price),
            risk=float(row.risk),
            entry_utc=row.entry_utc,
            exit_utc=row.exit_utc,
        )
        be_hits.append(hit)

    trades["or_width_pct"] = or_width
    trades["or_break_dist_r"] = or_break_dist_r
    trades["ema_gap_pct"] = ema_gap_pct
    trades["be_hit"] = np.array(be_hits, dtype=int)
    trades["r_lock"] = np.where(trades["be_hit"] == 1, 0.0, trades["r_no_lock"])
    trades["ret_lock"] = np.where(trades["be_hit"] == 1, 0.0, trades["ret_no_lock"])
    trades["system"] = "ORB"
    return trades


def _load_r6_dataset(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        run = pd.read_sql_query(
            """
            SELECT run_id
            FROM r6_strategy_runs
            WHERE status='completed' AND mode='research'
              AND start_date <= '2023-01-03' AND end_date >= '2026-02-23'
              AND symbols_csv LIKE '%SPY%'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            conn,
        )
        if run.empty:
            return pd.DataFrame()
        run_id = str(run.iloc[0]["run_id"])
        trades = pd.read_sql_query(
            """
            SELECT symbol, session_date, side, entry_ts, exit_ts, entry_price, risk, r_mult, ret_pct,
                   confidence, primary_horizon, include_today_or, inflation_factor, overlap_pairs_pct,
                   flat_regime, ref_width, break_confluence
            FROM r6_trades
            WHERE run_id=? AND variant_id=?
            ORDER BY symbol, session_date, entry_ts
            """,
            conn,
            params=[run_id, R6_BASE_VARIANT],
        )
        if trades.empty:
            return pd.DataFrame()
        symbols = sorted(trades["symbol"].dropna().unique().tolist())
        sym_ph = ",".join(["?"] * len(symbols))
        bars = pd.read_sql_query(
            f"""
            SELECT symbol, close_ts_cst, high, low
            FROM candles
            WHERE interval='15m' AND symbol IN ({sym_ph})
              AND close_ts_cst >= '2023-01-01T00:00:00-06:00'
              AND close_ts_cst <= '2026-12-31T23:59:59-06:00'
            ORDER BY symbol, close_ts_cst
            """,
            conn,
            params=symbols,
        )
    finally:
        conn.close()

    if bars.empty:
        return pd.DataFrame()

    bars["ts_utc"] = pd.to_datetime(bars["close_ts_cst"], utc=True, errors="coerce")
    bars = bars.dropna(subset=["ts_utc"]).sort_values(["symbol", "ts_utc"]).reset_index(drop=True)
    bars = bars.rename(columns={"high": "h", "low": "l"})
    bars_by_symbol = {s: g.copy() for s, g in bars.groupby("symbol", sort=False)}
    bar_arrays = _build_bar_arrays(bars_by_symbol)

    trades = trades.copy()
    trades["entry_utc"] = pd.to_datetime(trades["entry_ts"], utc=True, errors="coerce")
    trades["exit_utc"] = pd.to_datetime(trades["exit_ts"], utc=True, errors="coerce")
    trades = trades.dropna(subset=["entry_utc", "exit_utc"]).reset_index(drop=True)
    trades["entry_ct"] = trades["entry_utc"].dt.tz_convert("America/Chicago")
    trades["year"] = trades["entry_ct"].dt.year.astype(int)
    trades["entry_min_ct"] = trades["entry_ct"].dt.hour * 60 + trades["entry_ct"].dt.minute
    trades["side_short"] = (trades["side"].str.upper() == "SHORT").astype(int)
    trades["r_no_lock"] = pd.to_numeric(trades["r_mult"], errors="coerce").fillna(0.0)
    trades["ret_no_lock"] = pd.to_numeric(trades["ret_pct"], errors="coerce").fillna(0.0)
    trades["risk"] = pd.to_numeric(trades["risk"], errors="coerce").fillna(0.0)
    trades["entry_price"] = pd.to_numeric(trades["entry_price"], errors="coerce").fillna(0.0)
    for c in [
        "confidence",
        "primary_horizon",
        "include_today_or",
        "inflation_factor",
        "overlap_pairs_pct",
        "flat_regime",
        "ref_width",
        "break_confluence",
    ]:
        trades[c] = pd.to_numeric(trades[c], errors="coerce")

    be_hits = []
    for row in trades.itertuples(index=False):
        b_arr = bar_arrays.get(row.symbol)
        if b_arr is None:
            be_hits.append(0)
            continue
        hit = _compute_be_hit_fast(
            bar_arrays=b_arr,
            side=str(row.side).upper(),
            entry_price=float(row.entry_price),
            risk=float(row.risk),
            entry_utc=row.entry_utc,
            exit_utc=row.exit_utc,
        )
        be_hits.append(hit)

    trades["be_hit"] = np.array(be_hits, dtype=int)
    trades["r_lock"] = np.where(trades["be_hit"] == 1, 0.0, trades["r_no_lock"])
    trades["ret_lock"] = np.where(trades["be_hit"] == 1, 0.0, trades["ret_no_lock"])
    trades["system"] = "R6"
    return trades


def _build_orb_predicates(train: pd.DataFrame) -> list[Predicate]:
    preds: list[Predicate] = []
    for t in [10 * 60, 10 * 60 + 30, 11 * 60, 11 * 60 + 30, 12 * 60, 12 * 60 + 30]:
        preds.append(Predicate(name=f"entry_min_ct>={t}", fn=lambda df, tt=t: df["entry_min_ct"] >= tt))
    preds.append(Predicate(name="side_short==1", fn=lambda df: df["side_short"] == 1))
    preds.append(Predicate(name="side_short==0", fn=lambda df: df["side_short"] == 0))

    for col, qs in {
        "or_width_pct": [0.25, 0.4, 0.5, 0.6, 0.75],
        "or_break_dist_r": [0.25, 0.4, 0.5, 0.6, 0.75],
        "ema_gap_pct": [0.25, 0.4, 0.5, 0.6, 0.75],
    }.items():
        x = pd.to_numeric(train[col], errors="coerce").dropna()
        if x.empty:
            continue
        for q in qs:
            v = float(x.quantile(q))
            preds.append(Predicate(name=f"{col}<={v:.6g}", fn=lambda df, cc=col, vv=v: pd.to_numeric(df[cc], errors="coerce") <= vv))
            preds.append(Predicate(name=f"{col}>={v:.6g}", fn=lambda df, cc=col, vv=v: pd.to_numeric(df[cc], errors="coerce") >= vv))
    return preds


def _build_r6_predicates(train: pd.DataFrame) -> list[Predicate]:
    preds: list[Predicate] = []
    for t in [10 * 60, 10 * 60 + 30, 11 * 60, 11 * 60 + 30, 12 * 60, 12 * 60 + 30]:
        preds.append(Predicate(name=f"entry_min_ct>={t}", fn=lambda df, tt=t: df["entry_min_ct"] >= tt))
    preds.append(Predicate(name="side_short==1", fn=lambda df: df["side_short"] == 1))
    preds.append(Predicate(name="flat_regime==1", fn=lambda df: pd.to_numeric(df["flat_regime"], errors="coerce").fillna(0) == 1))
    preds.append(Predicate(name="include_today_or==0", fn=lambda df: pd.to_numeric(df["include_today_or"], errors="coerce").fillna(0) == 0))

    for col, qs in {
        "confidence": [0.25, 0.4, 0.5, 0.6, 0.75],
        "primary_horizon": [0.4, 0.6],
        "inflation_factor": [0.25, 0.4, 0.5, 0.6, 0.75],
        "overlap_pairs_pct": [0.25, 0.4, 0.5, 0.6, 0.75],
        "ref_width": [0.25, 0.4, 0.5, 0.6, 0.75],
    }.items():
        x = pd.to_numeric(train[col], errors="coerce").dropna()
        if x.empty:
            continue
        for q in qs:
            v = float(x.quantile(q))
            preds.append(Predicate(name=f"{col}<={v:.6g}", fn=lambda df, cc=col, vv=v: pd.to_numeric(df[cc], errors="coerce") <= vv))
            preds.append(Predicate(name=f"{col}>={v:.6g}", fn=lambda df, cc=col, vv=v: pd.to_numeric(df[cc], errors="coerce") >= vv))
    return preds


def _select_policy(train: pd.DataFrame, predicates: list[Predicate]) -> tuple[str, np.ndarray, dict]:
    candidates: list[tuple[str, np.ndarray, dict]] = []

    # Constants.
    mask_none = np.zeros(len(train), dtype=bool)
    m0 = _policy_metrics(train, mask_none)
    candidates.append(("lock_if=FALSE", mask_none, m0))

    mask_all = np.ones(len(train), dtype=bool)
    m1 = _policy_metrics(train, mask_all)
    candidates.append(("lock_if=TRUE", mask_all, m1))

    scored_predicates: list[tuple[Predicate, dict]] = []
    for p in predicates:
        try:
            mask = p.fn(train).fillna(False).to_numpy(dtype=bool)
        except Exception:
            continue
        m = _policy_metrics(train, mask)
        candidates.append((f"lock_if={p.name}", mask, m))
        scored_predicates.append((p, m))

    # Keep top singles before generating pair expressions to limit overfit/search space.
    scored_predicates.sort(key=lambda pm: _objective(pm[1]), reverse=True)
    top = [pm[0] for pm in scored_predicates[:12]]

    for p1, p2 in itertools.combinations(top, 2):
        try:
            b1 = p1.fn(train).fillna(False).to_numpy(dtype=bool)
            b2 = p2.fn(train).fillna(False).to_numpy(dtype=bool)
        except Exception:
            continue
        for op in ("AND", "OR"):
            mask = (b1 & b2) if op == "AND" else (b1 | b2)
            m = _policy_metrics(train, mask)
            rule = f"lock_if=({p1.name}) {op} ({p2.name})"
            candidates.append((rule, mask, m))

    candidates.sort(key=lambda r: _objective(r[2]), reverse=True)
    best_rule, best_mask, best_metrics = candidates[0]
    return best_rule, best_mask, best_metrics


def _evaluate_system(system: str, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict] = []
    detail_rows: list[dict] = []

    for train_years, test_year in TRAIN_TEST_FOLDS:
        train = df[df["year"].isin(train_years)].copy()
        test = df[df["year"] == test_year].copy()
        if train.empty or test.empty:
            continue

        if system == "ORB":
            preds = _build_orb_predicates(train)
        else:
            preds = _build_r6_predicates(train)

        best_rule, _, train_sel_metrics = _select_policy(train, preds)

        # Apply selected rule to test.
        if best_rule == "lock_if=FALSE":
            test_mask = np.zeros(len(test), dtype=bool)
        elif best_rule == "lock_if=TRUE":
            test_mask = np.ones(len(test), dtype=bool)
        else:
            expr = best_rule.replace("lock_if=", "")
            # Re-evaluate by matching predicate names.
            pred_map = {p.name: p for p in preds}
            if " AND " in expr:
                left, right = expr[1:-1].split(") AND (")
                test_mask = pred_map[left].fn(test).fillna(False).to_numpy(dtype=bool) & pred_map[right].fn(test).fillna(False).to_numpy(dtype=bool)
            elif " OR " in expr:
                left, right = expr[1:-1].split(") OR (")
                test_mask = pred_map[left].fn(test).fillna(False).to_numpy(dtype=bool) | pred_map[right].fn(test).fillna(False).to_numpy(dtype=bool)
            else:
                nm = expr.strip("()")
                test_mask = pred_map[nm].fn(test).fillna(False).to_numpy(dtype=bool)

        test_sel_metrics = _policy_metrics(test, test_mask)
        test_base_metrics = _policy_metrics(test, np.zeros(len(test), dtype=bool))
        test_all_lock_metrics = _policy_metrics(test, np.ones(len(test), dtype=bool))

        rows.append(
            {
                "system": system,
                "train_years": "-".join(map(str, train_years)),
                "test_year": int(test_year),
                "selected_rule": best_rule,
                "train_selected_comp_ret_pct": train_sel_metrics["comp_ret_pct"],
                "train_selected_avg_r": train_sel_metrics["avg_r"],
                "test_selected_comp_ret_pct": test_sel_metrics["comp_ret_pct"],
                "test_selected_avg_r": test_sel_metrics["avg_r"],
                "test_selected_pf": test_sel_metrics["pf"],
                "test_selected_trades": test_sel_metrics["trades"],
                "test_base_comp_ret_pct": test_base_metrics["comp_ret_pct"],
                "test_base_avg_r": test_base_metrics["avg_r"],
                "test_base_pf": test_base_metrics["pf"],
                "test_base_trades": test_base_metrics["trades"],
                "test_all_lock_comp_ret_pct": test_all_lock_metrics["comp_ret_pct"],
                "test_all_lock_avg_r": test_all_lock_metrics["avg_r"],
                "test_all_lock_pf": test_all_lock_metrics["pf"],
                "test_all_lock_trades": test_all_lock_metrics["trades"],
                "uplift_vs_base_comp_ret_pct": test_sel_metrics["comp_ret_pct"] - test_base_metrics["comp_ret_pct"],
                "uplift_vs_base_avg_r": test_sel_metrics["avg_r"] - test_base_metrics["avg_r"],
            }
        )

        test_out = test.copy()
        test_out["use_lock_025"] = test_mask.astype(int)
        test_out["r_selected"] = np.where(test_mask, test_out["r_lock"], test_out["r_no_lock"])
        test_out["ret_selected"] = np.where(test_mask, test_out["ret_lock"], test_out["ret_no_lock"])
        test_out["selected_rule"] = best_rule
        test_out["test_year"] = int(test_year)
        test_out["train_years"] = "-".join(map(str, train_years))
        detail_rows.extend(test_out.to_dict(orient="records"))

    return pd.DataFrame(rows), pd.DataFrame(detail_rows)


def _build_summary(fold_results: pd.DataFrame) -> dict:
    if fold_results.empty:
        return {"status": "no_results"}
    out: dict = {"status": "completed", "systems": {}}
    for sys, g in fold_results.groupby("system", sort=False):
        out["systems"][sys] = {
            "folds": int(len(g)),
            "avg_uplift_vs_base_comp_ret_pct": float(g["uplift_vs_base_comp_ret_pct"].mean()),
            "avg_uplift_vs_base_avg_r": float(g["uplift_vs_base_avg_r"].mean()),
            "folds_positive_uplift": int((g["uplift_vs_base_comp_ret_pct"] > 0).sum()),
            "selected_rules": g["selected_rule"].tolist(),
            "oos_selected_mean_comp_ret_pct": float(g["test_selected_comp_ret_pct"].mean()),
            "oos_base_mean_comp_ret_pct": float(g["test_base_comp_ret_pct"].mean()),
            "oos_all_lock_mean_comp_ret_pct": float(g["test_all_lock_comp_ret_pct"].mean()),
        }
    return out


def main() -> None:
    args = _parse_args()
    run(
        orb_db=args.orb_db,
        r6_db=args.r6_db,
        output_csv=args.output_csv,
        output_json=args.output_json,
        output_trades_csv=args.output_trades_csv,
    )


def run(
    orb_db: str = "./orb_research.db",
    r6_db: str = "./artifacts/orb_r6/orb_core.sqlite",
    output_csv: str = "./artifacts/reports/regime_lock_router_fold_results.csv",
    output_json: str = "./artifacts/reports/regime_lock_router_summary.json",
    output_trades_csv: str = "./artifacts/reports/regime_lock_router_trade_details.csv",
) -> dict:
    orb = _load_orb_dataset(orb_db)
    r6 = _load_r6_dataset(r6_db)

    fold_frames: list[pd.DataFrame] = []
    detail_frames: list[pd.DataFrame] = []
    if not orb.empty:
        f, d = _evaluate_system("ORB", orb)
        if not f.empty:
            fold_frames.append(f)
            detail_frames.append(d)
    if not r6.empty:
        f, d = _evaluate_system("R6", r6)
        if not f.empty:
            fold_frames.append(f)
            detail_frames.append(d)

    fold_results = pd.concat(fold_frames, ignore_index=True) if fold_frames else pd.DataFrame()
    trade_details = pd.concat(detail_frames, ignore_index=True) if detail_frames else pd.DataFrame()
    summary = _build_summary(fold_results)
    summary["inputs"] = {
        "orb_db": str(orb_db),
        "r6_db": str(r6_db),
        "orb_base_strategy": ORB_BASE_STRATEGY,
        "r6_base_variant": R6_BASE_VARIANT,
        "lock_threshold_r": LOCK_THRESHOLD_R,
        "folds": [{"train_years": tr, "test_year": te} for tr, te in TRAIN_TEST_FOLDS],
    }

    out_csv = Path(output_csv)
    out_json = Path(output_json)
    out_trades = Path(output_trades_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_trades.parent.mkdir(parents=True, exist_ok=True)

    fold_results.to_csv(out_csv, index=False)
    trade_details.to_csv(out_trades, index=False)
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    payload = {"summary": summary, "artifacts": {"fold_csv": str(out_csv), "trade_details_csv": str(out_trades), "summary_json": str(out_json)}}
    print(json.dumps(payload, indent=2))
    return payload


if __name__ == "__main__":
    main()
