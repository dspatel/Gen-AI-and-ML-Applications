from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .research import ResearchConfig, run_research


@dataclass(frozen=True)
class WalkForwardConfig:
    config_path: str
    test_end: str = "2026-02-23"
    symbols: list[str] | None = None


def run_walkforward(cfg: WalkForwardConfig) -> dict:
    train_split = ("train", "2023-01-03", "2024-12-31")
    validate_split = ("validate", "2025-01-01", "2025-12-31")
    test_split = ("test", "2026-01-01", cfg.test_end)

    train_summary = run_research(
        ResearchConfig(
            config_path=cfg.config_path,
            start_date=train_split[1],
            end_date=train_split[2],
            symbols=cfg.symbols,
        )
    )
    train_metrics = pd.read_csv(train_summary.get("artifacts", {}).get("metrics", "")) if train_summary.get("artifacts", {}).get("metrics") else pd.DataFrame()

    validate_summary = run_research(
        ResearchConfig(
            config_path=cfg.config_path,
            start_date=validate_split[1],
            end_date=validate_split[2],
            symbols=cfg.symbols,
        )
    )
    validate_metrics = (
        pd.read_csv(validate_summary.get("artifacts", {}).get("metrics", ""))
        if validate_summary.get("artifacts", {}).get("metrics")
        else pd.DataFrame()
    )

    locked_variant = _select_locked_variant(train_metrics, validate_metrics)
    if not locked_variant:
        locked_variant = _select_locked_variant(train_metrics, pd.DataFrame())
    if not locked_variant and not train_metrics.empty:
        locked_variant = str(train_metrics.iloc[0]["variant_id"])

    test_summary = run_research(
        ResearchConfig(
            config_path=cfg.config_path,
            start_date=test_split[1],
            end_date=test_split[2],
            symbols=cfg.symbols,
            variant_ids=[locked_variant] if locked_variant else None,
        )
    )
    test_metrics = pd.read_csv(test_summary.get("artifacts", {}).get("metrics", "")) if test_summary.get("artifacts", {}).get("metrics") else pd.DataFrame()

    split_results: list[dict] = [
        {
            "split": train_split[0],
            "start_date": train_split[1],
            "end_date": train_split[2],
            "run_id": train_summary.get("run_id"),
            "variants_tested": int(train_summary.get("variants_tested", 0)),
            "trades_count": int(train_summary.get("trades_count", 0)),
            "selected_variant": locked_variant,
            "variant_metrics": _variant_row(train_metrics, locked_variant),
            "artifacts": train_summary.get("artifacts", {}),
        },
        {
            "split": validate_split[0],
            "start_date": validate_split[1],
            "end_date": validate_split[2],
            "run_id": validate_summary.get("run_id"),
            "variants_tested": int(validate_summary.get("variants_tested", 0)),
            "trades_count": int(validate_summary.get("trades_count", 0)),
            "selected_variant": locked_variant,
            "variant_metrics": _variant_row(validate_metrics, locked_variant),
            "artifacts": validate_summary.get("artifacts", {}),
        },
        {
            "split": test_split[0],
            "start_date": test_split[1],
            "end_date": test_split[2],
            "run_id": test_summary.get("run_id"),
            "variants_tested": int(test_summary.get("variants_tested", 0)),
            "trades_count": int(test_summary.get("trades_count", 0)),
            "selected_variant": locked_variant,
            "variant_metrics": _variant_row(test_metrics, locked_variant),
            "artifacts": test_summary.get("artifacts", {}),
        },
    ]

    wf_df = pd.DataFrame(
        [
            {
                "split": r["split"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "selected_variant": r["selected_variant"],
                "trades_count": int(r.get("variant_metrics", {}).get("trades_count", 0) or 0),
                "win_rate": float(r.get("variant_metrics", {}).get("win_rate", 0.0) or 0.0),
                "avg_weighted_return_pct": float(r.get("variant_metrics", {}).get("avg_weighted_return_pct", 0.0) or 0.0),
                "total_return_pct": float(r.get("variant_metrics", {}).get("total_return_pct", 0.0) or 0.0),
                "profit_factor": float(r.get("variant_metrics", {}).get("profit_factor", 0.0) or 0.0),
                "max_drawdown_pct": float(r.get("variant_metrics", {}).get("max_drawdown_pct", 0.0) or 0.0),
            }
            for r in split_results
        ]
    )

    train_ablation = _build_ablation(train_metrics)

    out_dir = _resolve_wf_output_dir(split_results)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    wf_path = out_dir / f"ema20_walkforward_{ts}.csv"
    abl_entry_path = out_dir / f"ema20_ablation_entry_{ts}.csv"
    abl_exit_path = out_dir / f"ema20_ablation_exit_{ts}.csv"
    abl_lookback_path = out_dir / f"ema20_ablation_lookback_{ts}.csv"
    abl_flat_path = out_dir / f"ema20_ablation_flat_{ts}.csv"
    abl_chop_path = out_dir / f"ema20_ablation_chop_{ts}.csv"
    abl_max_open_path = out_dir / f"ema20_ablation_max_open_{ts}.csv"
    abl_max_new_path = out_dir / f"ema20_ablation_max_new_{ts}.csv"
    summary_path = out_dir / f"ema20_walkforward_summary_{ts}.json"

    wf_df.to_csv(wf_path, index=False)
    train_ablation["by_entry"].to_csv(abl_entry_path, index=False)
    train_ablation["by_exit"].to_csv(abl_exit_path, index=False)
    train_ablation["by_lookback"].to_csv(abl_lookback_path, index=False)
    train_ablation["by_flat"].to_csv(abl_flat_path, index=False)
    train_ablation["by_chop"].to_csv(abl_chop_path, index=False)
    train_ablation["by_max_open"].to_csv(abl_max_open_path, index=False)
    train_ablation["by_max_new"].to_csv(abl_max_new_path, index=False)

    summary = {
        "status": "completed",
        "locked_variant_from_train": locked_variant,
        "splits": split_results,
        "artifacts": {
            "walkforward": str(wf_path).replace("\\", "/"),
            "ablation_entry": str(abl_entry_path).replace("\\", "/"),
            "ablation_exit": str(abl_exit_path).replace("\\", "/"),
            "ablation_lookback": str(abl_lookback_path).replace("\\", "/"),
            "ablation_flat": str(abl_flat_path).replace("\\", "/"),
            "ablation_chop": str(abl_chop_path).replace("\\", "/"),
            "ablation_max_open": str(abl_max_open_path).replace("\\", "/"),
            "ablation_max_new": str(abl_max_new_path).replace("\\", "/"),
            "summary": str(summary_path).replace("\\", "/"),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def _variant_row(metrics: pd.DataFrame, variant_id: str) -> dict:
    if metrics.empty:
        return {}
    match = metrics[metrics["variant_id"] == variant_id]
    if match.empty:
        return {}
    return match.iloc[0].to_dict()


def _select_locked_variant(train_metrics: pd.DataFrame, validate_metrics: pd.DataFrame) -> str:
    if train_metrics.empty:
        return ""

    t = train_metrics.copy()
    for col in ["trades_count", "total_return_pct", "profit_factor", "max_drawdown_pct"]:
        if col in t.columns:
            t[col] = pd.to_numeric(t[col], errors="coerce")
    t = t.dropna(subset=["variant_id", "trades_count", "total_return_pct", "profit_factor", "max_drawdown_pct"])
    if t.empty:
        return ""

    if validate_metrics.empty:
        candidate = t[(t["trades_count"] >= 40) & (t["profit_factor"] >= 1.05)].copy()
        if candidate.empty:
            candidate = t[(t["trades_count"] >= 20) & (t["profit_factor"] >= 1.0)].copy()
        if candidate.empty:
            candidate = t.copy()

        candidate["robust_score"] = (
            candidate["total_return_pct"]
            - (0.75 * candidate["max_drawdown_pct"])
            + (20.0 * (candidate["profit_factor"] - 1.0))
        )
        candidate = candidate.sort_values(
            ["robust_score", "profit_factor", "total_return_pct", "max_drawdown_pct", "trades_count"],
            ascending=[False, False, False, True, False],
        )
        return str(candidate.iloc[0]["variant_id"]) if not candidate.empty else ""

    v = validate_metrics.copy()
    for col in ["trades_count", "total_return_pct", "profit_factor", "max_drawdown_pct"]:
        if col in v.columns:
            v[col] = pd.to_numeric(v[col], errors="coerce")
    v = v.dropna(subset=["variant_id", "trades_count", "total_return_pct", "profit_factor", "max_drawdown_pct"])
    if v.empty:
        return ""

    merged = t.merge(
        v[["variant_id", "trades_count", "total_return_pct", "profit_factor", "max_drawdown_pct"]].rename(
            columns={
                "trades_count": "val_trades_count",
                "total_return_pct": "val_total_return_pct",
                "profit_factor": "val_profit_factor",
                "max_drawdown_pct": "val_max_drawdown_pct",
            }
        ),
        on="variant_id",
        how="inner",
    )
    if merged.empty:
        return ""

    candidate = merged[
        (merged["trades_count"] >= 30)
        & (merged["val_trades_count"] >= 20)
        & (merged["profit_factor"] >= 1.0)
        & (merged["val_profit_factor"] >= 1.0)
        & (merged["val_total_return_pct"] > -10.0)
    ].copy()
    if candidate.empty:
        candidate = merged.copy()
    if candidate.empty:
        return ""

    candidate["robust_score"] = (
        (0.35 * candidate["total_return_pct"])
        + (0.65 * candidate["val_total_return_pct"])
        + (12.0 * (candidate["profit_factor"] - 1.0))
        + (20.0 * (candidate["val_profit_factor"] - 1.0))
        - (0.40 * candidate["max_drawdown_pct"])
        - (0.80 * candidate["val_max_drawdown_pct"])
    )
    candidate = candidate.sort_values(
        [
            "robust_score",
            "val_profit_factor",
            "val_total_return_pct",
            "profit_factor",
            "total_return_pct",
            "val_max_drawdown_pct",
            "max_drawdown_pct",
        ],
        ascending=[False, False, False, False, False, True, True],
    )
    return str(candidate.iloc[0]["variant_id"]) if not candidate.empty else ""


def _build_ablation(metrics: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if metrics.empty:
        empty = pd.DataFrame(columns=["key", "variants", "avg_total_return_pct", "avg_profit_factor", "avg_max_drawdown_pct"])
        return {"by_entry": empty, "by_exit": empty, "by_lookback": empty, "by_flat": empty}

    work = metrics.copy()
    parts = work["variant_id"].apply(_parse_variant_id)
    work["lookback"] = parts.apply(lambda x: x.get("lookback"))
    work["flat"] = parts.apply(lambda x: x.get("flat"))
    work["entry"] = parts.apply(lambda x: x.get("entry"))
    work["exit"] = parts.apply(lambda x: x.get("exit"))
    work["chop_max"] = parts.apply(lambda x: x.get("chop_max"))
    work["max_open"] = parts.apply(lambda x: x.get("max_open"))
    work["max_new"] = parts.apply(lambda x: x.get("max_new"))

    return {
        "by_entry": _agg(work, "entry"),
        "by_exit": _agg(work, "exit"),
        "by_lookback": _agg(work, "lookback"),
        "by_flat": _agg(work, "flat"),
        "by_chop": _agg(work, "chop_max"),
        "by_max_open": _agg(work, "max_open"),
        "by_max_new": _agg(work, "max_new"),
    }


def _agg(df: pd.DataFrame, key: str) -> pd.DataFrame:
    g = (
        df.groupby(key, dropna=False)
        .agg(
            variants=("variant_id", "count"),
            avg_total_return_pct=("total_return_pct", "mean"),
            avg_profit_factor=("profit_factor", "mean"),
            avg_max_drawdown_pct=("max_drawdown_pct", "mean"),
        )
        .reset_index()
        .rename(columns={key: "key"})
        .sort_values(["avg_total_return_pct"], ascending=False)
    )
    return g


def _parse_variant_id(text: str) -> dict[str, str]:
    s = str(text)
    tokens = s.split("__")
    base = tokens[0]
    m = re.match(r"^L(?P<lookback>\d+)_F(?P<flat>[0-9.]+)_(?P<entry>E\d+)_(?P<exit>X.+)$", base)
    if not m:
        return {
            "lookback": "",
            "flat": "",
            "entry": "",
            "exit": "",
            "chop_max": "",
            "max_open": "",
            "max_new": "",
        }
    d = m.groupdict()
    out = {
        "lookback": d.get("lookback", ""),
        "flat": d.get("flat", ""),
        "entry": d.get("entry", ""),
        "exit": d.get("exit", ""),
        "chop_max": "",
        "max_open": "",
        "max_new": "",
    }
    for token in tokens[1:]:
        if token.startswith("CH"):
            out["chop_max"] = token[2:]
        elif token.startswith("MO"):
            out["max_open"] = token[2:]
        elif token.startswith("ME"):
            out["max_new"] = token[2:]
    return out


def _resolve_wf_output_dir(split_results: list[dict]) -> Path:
    first_artifacts = split_results[0].get("artifacts", {}) if split_results else {}
    summary_path = first_artifacts.get("summary", "")
    if summary_path:
        p = Path(summary_path)
        return p.parent
    return Path("artifacts/ema20_stable/research")
