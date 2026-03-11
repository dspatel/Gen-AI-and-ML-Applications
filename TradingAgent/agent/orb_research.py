from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from agent.config import CHICAGO_TZ, OrbConfig
from agent.data import load_5m_data
from agent.db import Database
from agent.strategy.orb import run_orb_backtest


class OrbResearchEngine:
    def __init__(self, config: OrbConfig):
        self.config = config
        self.db = Database(Path(config.db_path))

    def run(self) -> dict:
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()

        try:
            bars_5m, provider = load_5m_data(
                symbol=self.config.symbol,
                start=self.config.start_date,
                end=self.config.end_date,
                provider=self.config.data_provider,
            )

            self.db.insert_run(
                run_id=run_id,
                started_at=started_at,
                mode=self.config.mode,
                symbol=self.config.symbol,
                start_date=self.config.start_date,
                end_date=self.config.end_date,
                provider=provider,
            )

            self.db.replace_table_rows(
                "bars_5m",
                "symbol = ? AND substr(ts, 1, 10) >= ? AND substr(ts, 1, 10) <= ?",
                (self.config.symbol, self.config.start_date, self.config.end_date),
                bars_5m,
            )

            trades, metrics, yearly = run_orb_backtest(symbol=self.config.symbol, bars_5m=bars_5m, run_id=run_id)
            self.db.replace_table_rows("trades", "run_id = ?", (run_id,), trades)
            self.db.replace_table_rows("metrics", "run_id = ?", (run_id,), metrics)

            summary = self._write_outputs(
                run_id=run_id,
                provider=provider,
                bars=bars_5m,
                trades=trades,
                metrics=metrics,
                yearly=yearly,
            )
            self.db.complete_run(run_id=run_id, status="completed", summary=summary)
            return summary
        except Exception as exc:
            failure = {
                "run_id": run_id,
                "status": "failed",
                "symbol": self.config.symbol,
                "error": str(exc),
            }
            self.db.complete_run(run_id=run_id, status="failed", summary=failure)
            raise

    def _write_outputs(
        self,
        run_id: str,
        provider: str,
        bars: pd.DataFrame,
        trades: pd.DataFrame,
        metrics: pd.DataFrame,
        yearly: pd.DataFrame,
    ) -> dict:
        trades_out = trades.sort_values(["strategy_id", "entry_ts"]) if not trades.empty else pd.DataFrame()
        metrics_out = metrics.sort_values("strategy_id") if not metrics.empty else pd.DataFrame()
        yearly_out = yearly.sort_values(["strategy_id", "year"]) if not yearly.empty else pd.DataFrame()
        side_out = self._build_side_performance(trades_out)

        if not trades_out.empty:
            entry_dt = pd.to_datetime(trades_out["entry_ts"], utc=True)
            exit_dt = pd.to_datetime(trades_out["exit_ts"], utc=True)
            trades_out["entry_ts_ct"] = entry_dt.dt.tz_convert(CHICAGO_TZ).dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            trades_out["exit_ts_ct"] = exit_dt.dt.tz_convert(CHICAGO_TZ).dt.strftime("%Y-%m-%d %H:%M:%S %Z")

        trades_out.to_csv("orb_trades.csv", index=False)
        metrics_out.to_csv("orb_experiment_metrics.csv", index=False)
        yearly_out.to_csv("orb_yearly_returns.csv", index=False)
        side_out.to_csv("orb_side_performance.csv", index=False)

        comparison = self._build_constraint_comparison(metrics_out)
        comparison.to_csv("orb_constraint_comparison.csv", index=False)

        best = None
        if not metrics_out.empty:
            ranked = metrics_out.sort_values(["total_return_pct", "avg_r", "profit_factor"], ascending=[False, False, False])
            best = ranked.iloc[0].to_dict()

        summary = {
            "run_id": run_id,
            "status": "completed",
            "symbol": self.config.symbol,
            "start_date": self.config.start_date,
            "end_date": self.config.end_date,
            "provider": provider,
            "bars_loaded_5m": int(len(bars)),
            "trades_total": int(len(trades_out)),
            "scenarios_tested": int(metrics_out.shape[0]),
            "best_scenario": best,
            "artifacts": {
                "summary": "orb_summary.json",
                "experiment_metrics": "orb_experiment_metrics.csv",
                "yearly_returns": "orb_yearly_returns.csv",
                "constraint_comparison": "orb_constraint_comparison.csv",
                "side_performance": "orb_side_performance.csv",
                "trades": "orb_trades.csv",
            },
        }

        with Path("orb_summary.json").open("w", encoding="utf-8") as fp:
            json.dump(summary, fp, indent=2, sort_keys=True)

        return summary

    def _build_constraint_comparison(self, metrics: pd.DataFrame) -> pd.DataFrame:
        if metrics.empty:
            return pd.DataFrame(
                columns=[
                    "timeframe_min",
                    "exit_variant",
                    "base_return_pct",
                    "limit1_improvement_pct",
                    "long_cutoff_improvement_pct",
                    "both_constraints_improvement_pct",
                ]
            )

        rows: list[dict] = []
        work = metrics.copy()
        work["exit_variant"] = work["strategy_id"].str.extract(r"^TF\d+_(.*)_(?:UNLIMITED|LIMIT1)_LONG_CUTOFF_.*$")

        for (tf, exit_variant), grp in work.groupby(["timeframe_min", "exit_variant"], sort=True):
            base = grp[(grp["trade_limit_1d"] == 0) & (grp["long_cutoff_ct"] == "NONE")]
            limit1 = grp[(grp["trade_limit_1d"] == 1) & (grp["long_cutoff_ct"] == "NONE")]
            cutoff = grp[(grp["trade_limit_1d"] == 0) & (grp["long_cutoff_ct"] != "NONE")]
            both = grp[(grp["trade_limit_1d"] == 1) & (grp["long_cutoff_ct"] != "NONE")]

            base_ret = float(base.iloc[0]["total_return_pct"]) if not base.empty else 0.0
            row = {
                "timeframe_min": int(tf),
                "exit_variant": str(exit_variant),
                "base_return_pct": base_ret,
                "limit1_improvement_pct": (float(limit1.iloc[0]["total_return_pct"]) - base_ret) if not limit1.empty else 0.0,
                "long_cutoff_improvement_pct": (float(cutoff.iloc[0]["total_return_pct"]) - base_ret) if not cutoff.empty else 0.0,
                "both_constraints_improvement_pct": (float(both.iloc[0]["total_return_pct"]) - base_ret) if not both.empty else 0.0,
            }
            rows.append(row)

        return pd.DataFrame(rows).sort_values(["timeframe_min", "exit_variant"])

    def _build_side_performance(self, trades: pd.DataFrame) -> pd.DataFrame:
        if trades.empty:
            return pd.DataFrame(
                columns=[
                    "strategy_id",
                    "side",
                    "trades_count",
                    "win_rate",
                    "avg_r",
                    "profit_factor",
                    "max_drawdown",
                    "total_return_pct",
                ]
            )

        rows: list[dict] = []
        for (strategy_id, side), grp in trades.groupby(["strategy_id", "side"], sort=True):
            g = grp.sort_values("exit_ts").copy()
            pnl = g["pnl"].astype(float)
            gross_profit = float(pnl[pnl > 0].sum())
            gross_loss = abs(float(pnl[pnl < 0].sum()))
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

            equity = pnl.cumsum()
            drawdown = equity - equity.cummax()
            max_drawdown = abs(float(drawdown.min())) if len(drawdown) else 0.0

            total_return_pct = float((1.0 + g["ret_pct"]).prod() - 1.0) * 100.0
            rows.append(
                {
                    "strategy_id": strategy_id,
                    "side": side,
                    "trades_count": int(len(g)),
                    "win_rate": float((g["r_mult"] > 0).mean()),
                    "avg_r": float(g["r_mult"].mean()),
                    "profit_factor": float(profit_factor),
                    "max_drawdown": max_drawdown,
                    "total_return_pct": total_return_pct,
                }
            )

        return pd.DataFrame(rows).sort_values(["strategy_id", "side"]).reset_index(drop=True)
