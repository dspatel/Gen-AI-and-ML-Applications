from __future__ import annotations

import argparse
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252.0
DEFAULT_ENGINE_PATH = Path(__file__).resolve().parent.parent / "ORB_TEST" / "backtest_orb_shared_cash.py"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "data" / "market_data_cache.sqlite"


def _load_module_from_path(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward HMM regime comparison (baseline/global/per-symbol/hybrid).")
    p.add_argument("--engine-path", default=str(DEFAULT_ENGINE_PATH))
    p.add_argument("--symbols", default="QQQ,NVDA,SPY")
    p.add_argument("--symbols-file", default="")
    p.add_argument("--data-source", choices=["yfinance", "tvdatafeed", "alpaca"], default="alpaca")
    p.add_argument("--period", default="420d")
    p.add_argument("--interval", default="15m")
    p.add_argument("--tv-n-bars", type=int, default=12000)
    p.add_argument("--tv-exchanges", default="QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX")
    p.add_argument("--tv-default-exchange", default="NASDAQ")
    p.add_argument("--tv-username", default="")
    p.add_argument("--tv-password", default="")
    p.add_argument("--alpaca-key", default="")
    p.add_argument("--alpaca-secret", default="")
    p.add_argument("--alpaca-feed", choices=["iex", "sip", "otc"], default="iex")
    p.add_argument("--alpaca-base-url", default="https://data.alpaca.markets")
    p.add_argument("--cache-db", default=str(DEFAULT_CACHE_PATH))
    p.add_argument("--cache-refresh", action="store_true")
    p.add_argument("--no-cache", action="store_true")

    p.add_argument("--cash-per-symbol", type=float, default=10000.0)
    p.add_argument("--start-shares-each", type=int, default=100)
    p.add_argument("--trade-fraction", type=float, default=0.2)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--commission-per-share", type=float, default=0.005)
    p.add_argument("--train-days", type=int, default=100)
    p.add_argument("--test-days", type=int, default=20)
    p.add_argument("--step-days", type=int, default=20)
    p.add_argument("--min-symbols", type=int, default=10)
    p.add_argument("--min-history-days", type=int, default=60)
    p.add_argument("--target-growth-pct", type=float, default=5.0)
    p.add_argument("--htf-gate", choices=["none", "monthly", "weekly", "both"], default="weekly")

    p.add_argument("--hmm-global-symbol", default="SPY")
    p.add_argument("--hmm-states", type=int, default=3)
    p.add_argument("--hmm-min-train-samples", type=int, default=80)
    p.add_argument("--hmm-min-state-trades", type=int, default=10)
    p.add_argument("--hmm-random-state", type=int, default=42)

    p.add_argument("--save-fold-csv", default="")
    p.add_argument("--save-summary-csv", default="")
    return p.parse_args()


def _fit_hmm_state_map(
    context_df: pd.DataFrame,
    train_dates: list[str],
    pred_dates: list[str],
    n_states: int,
    min_train_samples: int,
    random_state: int,
) -> tuple[dict[str, int], bool]:
    try:
        from hmmlearn.hmm import GaussianHMM  # type: ignore
        from sklearn.preprocessing import StandardScaler
    except Exception:
        return {}, False

    feat_cols = ["week_ret", "month_ret", "prev_vol_ratio", "prev_range_pct", "trend_above_ema20"]
    w = context_df.copy()
    w["session_date"] = w["session_date"].astype(str)
    w = w.sort_values("session_date").reset_index(drop=True)
    w = w[w["session_date"].isin(set(train_dates) | set(pred_dates))].copy()
    if w.empty:
        return {}, False

    for c in feat_cols:
        w[c] = pd.to_numeric(w[c], errors="coerce")

    tr = w[w["session_date"].isin(train_dates)].dropna(subset=feat_cols)
    if len(tr) < int(min_train_samples):
        return {}, False

    scaler = StandardScaler()
    x_train = scaler.fit_transform(tr[feat_cols].to_numpy(dtype=float))

    model = GaussianHMM(
        n_components=max(int(n_states), 2),
        covariance_type="diag",
        n_iter=250,
        random_state=int(random_state),
    )
    try:
        model.fit(x_train)
    except Exception:
        return {}, False

    pr = w[w["session_date"].isin(pred_dates)].dropna(subset=feat_cols).copy()
    if pr.empty:
        return {}, True
    x_pred = scaler.transform(pr[feat_cols].to_numpy(dtype=float))

    states = np.full(len(pr), np.nan)
    for i in range(len(pr)):
        try:
            # Online-style decoding to avoid using future observations.
            states[i] = float(model.predict(x_pred[: i + 1])[-1])
        except Exception:
            states[i] = np.nan

    pr["state"] = states
    pr = pr.dropna(subset=["state"]).copy()
    out = dict(zip(pr["session_date"].astype(str), pr["state"].astype(int)))
    return out, True


def _attach_state_columns(
    df: pd.DataFrame,
    global_state_map: dict[str, int],
    symbol_state_maps: dict[str, dict[str, int]],
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["state_global"] = out["session_date"].astype(str).map(global_state_map)

    def _sym_state(r) -> float:
        sym = str(r["symbol"])
        dt = str(r["session_date"])
        m = symbol_state_maps.get(sym, {})
        if dt in m:
            return float(m[dt])
        return float("nan")

    out["state_symbol"] = out.apply(_sym_state, axis=1)
    out["state_hybrid"] = out["state_symbol"]
    miss = out["state_hybrid"].isna()
    out.loc[miss, "state_hybrid"] = out.loc[miss, "state_global"]
    return out


def _derive_allowed_states(
    train_df: pd.DataFrame,
    state_col: str,
    min_state_trades: int,
) -> dict[int, set[int] | None]:
    out: dict[int, set[int] | None] = {0: None, 1: None}
    if train_df.empty or state_col not in train_df.columns:
        return out

    w = train_df.copy()
    w = w.dropna(subset=[state_col, "direction_up", "edge_per_share"])
    if w.empty:
        return out
    w[state_col] = pd.to_numeric(w[state_col], errors="coerce")
    w = w.dropna(subset=[state_col])
    w[state_col] = w[state_col].astype(int)

    for d in [0, 1]:
        part = w[w["direction_up"] == d]
        if part.empty:
            out[d] = None
            continue
        g = part.groupby(state_col, as_index=False).agg(n=("edge_per_share", "size"), mean_edge=("edge_per_share", "mean"))
        keep = set(g[(g["n"] >= int(min_state_trades)) & (g["mean_edge"] > 0.0)][state_col].astype(int).tolist())
        out[d] = keep if keep else None
    return out


def _apply_allowed_state_gate(
    df: pd.DataFrame,
    state_col: str,
    allowed_by_dir: dict[int, set[int] | None],
) -> pd.DataFrame:
    if df.empty or state_col not in df.columns:
        return df
    keep = np.ones(len(df), dtype=bool)
    states = pd.to_numeric(df[state_col], errors="coerce")
    dirs = pd.to_numeric(df["direction_up"], errors="coerce").fillna(0).astype(int)

    for i in range(len(df)):
        d = int(dirs.iloc[i])
        allow = allowed_by_dir.get(d, None)
        if allow is None:
            continue
        st = states.iloc[i]
        keep[i] = (not math.isnan(float(st))) and (int(st) in allow)
    return df.loc[keep].copy()


def _annualized_growth(cash_change: float, start_cash_total: float, covered_test_days: int) -> float:
    if start_cash_total <= 0 or covered_test_days <= 0:
        return float("nan")
    gross = 1.0 + (cash_change / start_cash_total)
    if gross <= 0:
        return float("nan")
    return (gross ** (TRADING_DAYS_PER_YEAR / covered_test_days) - 1.0) * 100.0


def main() -> None:
    args = _parse_args()
    helper = _load_module_from_path(Path(__file__).with_name("walk_forward_ml_filter.py"), "orb_hybrid_ml")
    orb = helper._load_orb_module(args.engine_path)

    symbols, bars_by_symbol, common_dates, dropped = helper._load_symbols_data_robust(orb, args)
    if not symbols:
        raise RuntimeError("No symbols with market data.")

    presets = helper._preset_pool()
    preset_names = [p["name"] for p in presets]
    context_by_symbol = {s: helper._build_daily_context_from_bars(bars_by_symbol[s]) for s in symbols}

    global_symbol = str(args.hmm_global_symbol).upper().strip()
    if global_symbol not in symbols:
        global_symbol = symbols[0]

    rows: list[dict] = []
    all_test_dates: set[str] = set()
    i = 0
    while i + args.train_days + args.test_days <= len(common_dates):
        train_dates = common_dates[i : i + args.train_days]
        test_dates = common_dates[i + args.train_days : i + args.train_days + args.test_days]
        fold_dates = train_dates + test_dates
        all_test_dates.update(test_dates)

        train_parts: list[pd.DataFrame] = []
        test_parts: list[pd.DataFrame] = []
        for sym in symbols:
            train_parts.append(
                helper._collect_candidates_for_symbol(
                    orb,
                    symbol=sym,
                    bars=bars_by_symbol[sym],
                    dates=train_dates,
                    context_df=context_by_symbol[sym],
                    args=args,
                    presets=presets,
                )
            )
            test_parts.append(
                helper._collect_candidates_for_symbol(
                    orb,
                    symbol=sym,
                    bars=bars_by_symbol[sym],
                    dates=test_dates,
                    context_df=context_by_symbol[sym],
                    args=args,
                    presets=presets,
                )
            )

        train_cands = pd.concat([d for d in train_parts if not d.empty], ignore_index=True) if train_parts else pd.DataFrame()
        test_cands = pd.concat([d for d in test_parts if not d.empty], ignore_index=True) if test_parts else pd.DataFrame()
        train_cands = helper._apply_htf_direction_gate(train_cands, args.htf_gate)
        test_cands = helper._apply_htf_direction_gate(test_cands, args.htf_gate)

        if train_cands.empty or test_cands.empty:
            rows.append(
                {
                    "fold": len(rows) + 1,
                    "train_start": train_dates[0],
                    "train_end": train_dates[-1],
                    "test_start": test_dates[0],
                    "test_end": test_dates[-1],
                    "baseline_global_preset": "NONE",
                    "hmm_global_symbol": global_symbol,
                    "symbol_hmm_models": 0,
                    "test_cash_change_baseline": 0.0,
                    "test_cash_change_hmm_global": 0.0,
                    "test_cash_change_hmm_symbol": 0.0,
                    "test_cash_change_hmm_hybrid": 0.0,
                    "test_trades_baseline": 0,
                    "test_trades_hmm_global": 0,
                    "test_trades_hmm_symbol": 0,
                    "test_trades_hmm_hybrid": 0,
                }
            )
            i += args.step_days
            continue

        train_cands["dummy_score"] = 1.0
        test_cands["dummy_score"] = 1.0

        best_preset = "baseline_close"
        best_train_cash = -10**18
        for pname in preset_names:
            sim = helper._simulate_selected_candidates(
                cands=train_cands[train_cands["preset"] == pname],
                symbols=symbols,
                score_col="dummy_score",
                threshold=0.0,
                start_cash_per_symbol=args.cash_per_symbol,
                start_shares_each=args.start_shares_each,
                trade_fraction=args.trade_fraction,
                slippage_bps=args.slippage_bps,
                commission_per_share=args.commission_per_share,
            )
            if sim["cash_change"] > best_train_cash:
                best_train_cash = sim["cash_change"]
                best_preset = pname

        baseline_train = train_cands[train_cands["preset"] == best_preset].copy()
        baseline_test = test_cands[test_cands["preset"] == best_preset].copy()

        baseline_test_sim = helper._simulate_selected_candidates(
            cands=baseline_test,
            symbols=symbols,
            score_col="dummy_score",
            threshold=0.0,
            start_cash_per_symbol=args.cash_per_symbol,
            start_shares_each=args.start_shares_each,
            trade_fraction=args.trade_fraction,
            slippage_bps=args.slippage_bps,
            commission_per_share=args.commission_per_share,
        )

        global_state_map, global_ok = _fit_hmm_state_map(
            context_df=context_by_symbol[global_symbol],
            train_dates=train_dates,
            pred_dates=fold_dates,
            n_states=args.hmm_states,
            min_train_samples=args.hmm_min_train_samples,
            random_state=args.hmm_random_state,
        )

        symbol_state_maps: dict[str, dict[str, int]] = {}
        symbol_models = 0
        for sym in symbols:
            m, ok = _fit_hmm_state_map(
                context_df=context_by_symbol[sym],
                train_dates=train_dates,
                pred_dates=fold_dates,
                n_states=args.hmm_states,
                min_train_samples=args.hmm_min_train_samples,
                random_state=args.hmm_random_state + (abs(hash(sym)) % 10000),
            )
            if ok and m:
                symbol_state_maps[sym] = m
                symbol_models += 1

        baseline_train = _attach_state_columns(baseline_train, global_state_map if global_ok else {}, symbol_state_maps)
        baseline_test = _attach_state_columns(baseline_test, global_state_map if global_ok else {}, symbol_state_maps)

        mode_to_state_col = {
            "hmm_global": "state_global",
            "hmm_symbol": "state_symbol",
            "hmm_hybrid": "state_hybrid",
        }
        mode_sims: dict[str, dict] = {}
        for mode, state_col in mode_to_state_col.items():
            allowed = _derive_allowed_states(
                train_df=baseline_train,
                state_col=state_col,
                min_state_trades=args.hmm_min_state_trades,
            )
            gated_test = _apply_allowed_state_gate(
                df=baseline_test,
                state_col=state_col,
                allowed_by_dir=allowed,
            )
            if gated_test.empty:
                mode_sims[mode] = {"cash_change": 0.0, "trades_taken": 0}
                continue
            gated_test["dummy_score"] = 1.0
            mode_sims[mode] = helper._simulate_selected_candidates(
                cands=gated_test,
                symbols=symbols,
                score_col="dummy_score",
                threshold=0.0,
                start_cash_per_symbol=args.cash_per_symbol,
                start_shares_each=args.start_shares_each,
                trade_fraction=args.trade_fraction,
                slippage_bps=args.slippage_bps,
                commission_per_share=args.commission_per_share,
            )

        rows.append(
            {
                "fold": len(rows) + 1,
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "baseline_global_preset": best_preset,
                "hmm_global_symbol": global_symbol,
                "symbol_hmm_models": int(symbol_models),
                "test_cash_change_baseline": baseline_test_sim["cash_change"],
                "test_cash_change_hmm_global": mode_sims["hmm_global"]["cash_change"],
                "test_cash_change_hmm_symbol": mode_sims["hmm_symbol"]["cash_change"],
                "test_cash_change_hmm_hybrid": mode_sims["hmm_hybrid"]["cash_change"],
                "test_trades_baseline": baseline_test_sim["trades_taken"],
                "test_trades_hmm_global": mode_sims["hmm_global"]["trades_taken"],
                "test_trades_hmm_symbol": mode_sims["hmm_symbol"]["trades_taken"],
                "test_trades_hmm_hybrid": mode_sims["hmm_hybrid"]["trades_taken"],
            }
        )

        i += args.step_days

    if not rows:
        raise RuntimeError("No folds generated.")

    fold_df = pd.DataFrame(rows)
    covered_test_days = int(len(all_test_dates))
    start_cash_total = float(args.cash_per_symbol * len(symbols))

    mode_cols = {
        "baseline": ("test_cash_change_baseline", "test_trades_baseline"),
        "hmm_global": ("test_cash_change_hmm_global", "test_trades_hmm_global"),
        "hmm_symbol": ("test_cash_change_hmm_symbol", "test_trades_hmm_symbol"),
        "hmm_hybrid": ("test_cash_change_hmm_hybrid", "test_trades_hmm_hybrid"),
    }
    summary_rows: list[dict] = []
    for mode, (cash_col, trades_col) in mode_cols.items():
        sum_cash = float(fold_df[cash_col].sum())
        summary_rows.append(
            {
                "mode": mode,
                "total_cash_change": sum_cash,
                "annualized_cash_growth_pct": _annualized_growth(sum_cash, start_cash_total, covered_test_days),
                "positive_folds": int((fold_df[cash_col] > 0).sum()),
                "folds": int(len(fold_df)),
                "trades_total": int(fold_df[trades_col].sum()),
            }
        )
    summary = pd.DataFrame(summary_rows)
    summary["meets_target"] = summary["annualized_cash_growth_pct"] >= float(args.target_growth_pct)

    print("=== Walk-Forward HMM Modes Setup ===")
    print(
        f"symbols={len(symbols)} dropped={len(dropped)} data_source={args.data_source} "
        f"train_days={args.train_days} test_days={args.test_days} step_days={args.step_days} "
        f"htf_gate={args.htf_gate} min_history_days={args.min_history_days} "
        f"slippage_bps={args.slippage_bps} commission_per_share={args.commission_per_share}"
    )
    if dropped:
        print(f"dropped_symbols={','.join(dropped)}")
    print(f"hmm_states={args.hmm_states} hmm_min_train_samples={args.hmm_min_train_samples} hmm_min_state_trades={args.hmm_min_state_trades}")
    print(f"hmm_global_symbol={global_symbol}")
    print(f"window_start={common_dates[0]} window_end={common_dates[-1]}")
    print(f"covered_test_days={covered_test_days}")
    print(f"target_growth_pct={args.target_growth_pct:.2f}")

    print("\n=== Fold Results ===")
    print(fold_df.to_string(index=False))

    print("\n=== Summary ===")
    print(summary.to_string(index=False))

    if args.save_fold_csv.strip():
        out = Path(args.save_fold_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        fold_df.to_csv(out, index=False)
        print(f"\nSaved folds CSV: {out}")
    if args.save_summary_csv.strip():
        out = Path(args.save_summary_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(out, index=False)
        print(f"Saved summary CSV: {out}")


if __name__ == "__main__":
    main()
