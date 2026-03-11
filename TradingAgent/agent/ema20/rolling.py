from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .research import ResearchConfig, run_research
from .walkforward import _select_locked_variant


@dataclass(frozen=True)
class RollingWalkForwardConfig:
    config_path: str
    start_date: str = "2023-01-03"
    end_date: str = "2026-02-23"
    train_months: int = 18
    validate_months: int = 6
    test_months: int = 3
    step_months: int = 6
    min_test_pf: float = 1.20
    max_test_dd_pct: float = 30.0
    min_test_trades: int = 5
    min_test_excess_vs_buyhold_pct: float = 0.0
    symbols: list[str] | None = None


def run_rolling_walkforward(cfg: RollingWalkForwardConfig) -> dict:
    for name, value in [
        ("train_months", cfg.train_months),
        ("validate_months", cfg.validate_months),
        ("test_months", cfg.test_months),
        ("step_months", cfg.step_months),
    ]:
        if int(value) <= 0:
            raise ValueError(f"{name} must be > 0")

    folds = _build_folds(
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        train_months=int(cfg.train_months),
        validate_months=int(cfg.validate_months),
        test_months=int(cfg.test_months),
        step_months=int(cfg.step_months),
    )
    if not folds:
        raise ValueError("No rolling folds produced with current date range/window sizes")

    fold_rows: list[dict] = []
    split_rows: list[dict] = []
    split_artifacts: list[dict] = []

    for fold_idx, fold in enumerate(folds, start=1):
        train_summary = run_research(
            ResearchConfig(
                config_path=cfg.config_path,
                start_date=fold["train_start"],
                end_date=fold["train_end"],
                symbols=cfg.symbols,
            )
        )
        train_metrics = _load_metrics(train_summary)

        validate_summary = run_research(
            ResearchConfig(
                config_path=cfg.config_path,
                start_date=fold["validate_start"],
                end_date=fold["validate_end"],
                symbols=cfg.symbols,
            )
        )
        validate_metrics = _load_metrics(validate_summary)

        locked_variant = _select_locked_variant(train_metrics, validate_metrics)
        if not locked_variant:
            locked_variant = _select_locked_variant(train_metrics, pd.DataFrame())
        if not locked_variant and not train_metrics.empty:
            locked_variant = str(train_metrics.iloc[0]["variant_id"])

        test_summary = run_research(
            ResearchConfig(
                config_path=cfg.config_path,
                start_date=fold["test_start"],
                end_date=fold["test_end"],
                symbols=cfg.symbols,
                variant_ids=[locked_variant] if locked_variant else None,
            )
        )
        test_metrics = _load_metrics(test_summary)

        train_row = _variant_row(train_metrics, locked_variant)
        validate_row = _variant_row(validate_metrics, locked_variant)
        test_row = _variant_row(test_metrics, locked_variant)

        test_pass = _test_gate_passes(
            test_row=test_row,
            min_pf=float(cfg.min_test_pf),
            max_dd_pct=float(cfg.max_test_dd_pct),
            min_trades=int(cfg.min_test_trades),
            min_excess_vs_buyhold_pct=float(cfg.min_test_excess_vs_buyhold_pct),
        )

        fold_rows.append(
            {
                "fold": int(fold_idx),
                "train_start": fold["train_start"],
                "train_end": fold["train_end"],
                "validate_start": fold["validate_start"],
                "validate_end": fold["validate_end"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "selected_variant": locked_variant,
                "train_trades": int(train_row.get("trades_count", 0) or 0),
                "train_total_return_pct": float(train_row.get("total_return_pct", 0.0) or 0.0),
                "train_profit_factor": float(train_row.get("profit_factor", 0.0) or 0.0),
                "train_max_drawdown_pct": float(train_row.get("max_drawdown_pct", 0.0) or 0.0),
                "validate_trades": int(validate_row.get("trades_count", 0) or 0),
                "validate_total_return_pct": float(validate_row.get("total_return_pct", 0.0) or 0.0),
                "validate_profit_factor": float(validate_row.get("profit_factor", 0.0) or 0.0),
                "validate_max_drawdown_pct": float(validate_row.get("max_drawdown_pct", 0.0) or 0.0),
                "test_trades": int(test_row.get("trades_count", 0) or 0),
                "test_total_return_pct": float(test_row.get("total_return_pct", 0.0) or 0.0),
                "test_profit_factor": float(test_row.get("profit_factor", 0.0) or 0.0),
                "test_max_drawdown_pct": float(test_row.get("max_drawdown_pct", 0.0) or 0.0),
                "test_win_rate": float(test_row.get("win_rate", 0.0) or 0.0),
                "test_buyhold_equal_weight_return_pct": float(test_row.get("buyhold_equal_weight_return_pct", 0.0) or 0.0),
                "test_excess_vs_buyhold_equal_weight_pct": float(test_row.get("excess_vs_buyhold_equal_weight_pct", 0.0) or 0.0),
                "test_pass_gate": bool(test_pass),
            }
        )

        split_rows.extend(
            [
                _split_row(fold_idx, "train", fold["train_start"], fold["train_end"], locked_variant, train_row, train_summary),
                _split_row(
                    fold_idx,
                    "validate",
                    fold["validate_start"],
                    fold["validate_end"],
                    locked_variant,
                    validate_row,
                    validate_summary,
                ),
                _split_row(fold_idx, "test", fold["test_start"], fold["test_end"], locked_variant, test_row, test_summary),
            ]
        )
        split_artifacts.extend(
            [
                train_summary.get("artifacts", {}),
                validate_summary.get("artifacts", {}),
                test_summary.get("artifacts", {}),
            ]
        )

    folds_df = pd.DataFrame(fold_rows)
    splits_df = pd.DataFrame(split_rows)

    out_dir = _resolve_output_dir(split_artifacts)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    folds_path = out_dir / f"ema20_rolling_folds_{ts}.csv"
    splits_path = out_dir / f"ema20_rolling_splits_{ts}.csv"
    summary_path = out_dir / f"ema20_rolling_summary_{ts}.json"

    folds_df.to_csv(folds_path, index=False)
    splits_df.to_csv(splits_path, index=False)

    pass_count = int(folds_df["test_pass_gate"].sum()) if not folds_df.empty else 0
    total_folds = int(len(folds_df))

    summary = {
        "status": "completed",
        "config_path": cfg.config_path,
        "start_date": cfg.start_date,
        "end_date": cfg.end_date,
        "window_months": {
            "train": int(cfg.train_months),
            "validate": int(cfg.validate_months),
            "test": int(cfg.test_months),
            "step": int(cfg.step_months),
        },
        "gate": {
            "min_test_pf": float(cfg.min_test_pf),
            "max_test_dd_pct": float(cfg.max_test_dd_pct),
            "min_test_trades": int(cfg.min_test_trades),
            "min_test_excess_vs_buyhold_pct": float(cfg.min_test_excess_vs_buyhold_pct),
        },
        "folds_count": total_folds,
        "test_pass_count": pass_count,
        "test_pass_rate": (float(pass_count) / float(total_folds)) if total_folds > 0 else 0.0,
        "avg_test_total_return_pct": float(folds_df["test_total_return_pct"].mean()) if not folds_df.empty else 0.0,
        "avg_test_profit_factor": float(folds_df["test_profit_factor"].mean()) if not folds_df.empty else 0.0,
        "avg_test_max_drawdown_pct": float(folds_df["test_max_drawdown_pct"].mean()) if not folds_df.empty else 0.0,
        "avg_test_excess_vs_buyhold_equal_weight_pct": float(folds_df["test_excess_vs_buyhold_equal_weight_pct"].mean()) if not folds_df.empty else 0.0,
        "artifacts": {
            "folds": str(folds_path).replace("\\", "/"),
            "splits": str(splits_path).replace("\\", "/"),
            "summary": str(summary_path).replace("\\", "/"),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _build_folds(
    start_date: str,
    end_date: str,
    train_months: int,
    validate_months: int,
    test_months: int,
    step_months: int,
) -> list[dict[str, str]]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    folds: list[dict[str, str]] = []

    cursor = start
    while True:
        train_start = cursor
        train_end = (train_start + pd.DateOffset(months=train_months)) - pd.Timedelta(days=1)
        validate_start = train_end + pd.Timedelta(days=1)
        validate_end = (validate_start + pd.DateOffset(months=validate_months)) - pd.Timedelta(days=1)
        test_start = validate_end + pd.Timedelta(days=1)
        if test_start > end:
            break
        test_end = min((test_start + pd.DateOffset(months=test_months)) - pd.Timedelta(days=1), end)

        folds.append(
            {
                "train_start": train_start.date().isoformat(),
                "train_end": train_end.date().isoformat(),
                "validate_start": validate_start.date().isoformat(),
                "validate_end": validate_end.date().isoformat(),
                "test_start": test_start.date().isoformat(),
                "test_end": test_end.date().isoformat(),
            }
        )

        cursor = cursor + pd.DateOffset(months=step_months)
        if cursor > end:
            break

    return folds


def _load_metrics(summary: dict) -> pd.DataFrame:
    path = str(summary.get("artifacts", {}).get("metrics", "") or "")
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


def _variant_row(metrics: pd.DataFrame, variant_id: str) -> dict:
    if metrics.empty:
        return {}
    if not variant_id:
        return metrics.iloc[0].to_dict()
    match = metrics[metrics["variant_id"] == variant_id]
    if match.empty:
        return {}
    return match.iloc[0].to_dict()


def _split_row(
    fold_idx: int,
    split: str,
    start_date: str,
    end_date: str,
    selected_variant: str,
    variant_metrics: dict,
    summary: dict,
) -> dict:
    return {
        "fold": int(fold_idx),
        "split": split,
        "start_date": start_date,
        "end_date": end_date,
        "selected_variant": selected_variant,
        "trades_count": int(variant_metrics.get("trades_count", 0) or 0),
        "win_rate": float(variant_metrics.get("win_rate", 0.0) or 0.0),
        "avg_weighted_return_pct": float(variant_metrics.get("avg_weighted_return_pct", 0.0) or 0.0),
        "total_return_pct": float(variant_metrics.get("total_return_pct", 0.0) or 0.0),
        "profit_factor": float(variant_metrics.get("profit_factor", 0.0) or 0.0),
        "max_drawdown_pct": float(variant_metrics.get("max_drawdown_pct", 0.0) or 0.0),
        "buyhold_equal_weight_return_pct": float(variant_metrics.get("buyhold_equal_weight_return_pct", 0.0) or 0.0),
        "excess_vs_buyhold_equal_weight_pct": float(variant_metrics.get("excess_vs_buyhold_equal_weight_pct", 0.0) or 0.0),
        "run_id": str(summary.get("run_id", "")),
        "variants_tested": int(summary.get("variants_tested", 0) or 0),
    }


def _test_gate_passes(
    test_row: dict,
    min_pf: float,
    max_dd_pct: float,
    min_trades: int,
    min_excess_vs_buyhold_pct: float,
) -> bool:
    trades = int(test_row.get("trades_count", 0) or 0)
    pf = float(test_row.get("profit_factor", 0.0) or 0.0)
    dd = float(test_row.get("max_drawdown_pct", 0.0) or 0.0)
    excess = float(test_row.get("excess_vs_buyhold_equal_weight_pct", 0.0) or 0.0)
    return (
        trades >= int(min_trades)
        and pf >= float(min_pf)
        and dd <= float(max_dd_pct)
        and excess >= float(min_excess_vs_buyhold_pct)
    )


def _resolve_output_dir(artifacts: list[dict]) -> Path:
    for a in artifacts:
        summary_path = str(a.get("summary", "") or "")
        if summary_path:
            return Path(summary_path).parent
    return Path("artifacts/ema20_stable/research")
