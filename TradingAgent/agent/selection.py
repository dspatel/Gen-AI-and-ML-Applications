from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from agent.data import load_5m_data
from agent.db import Database
from agent.strategy.orb import run_orb_backtest
from agent.strategy_spec import StrategySpec, parse_strategy_id


@dataclass(frozen=True)
class SelectionConfig:
    symbols: list[str]
    asof_date: str
    frequency: str  # monthly, quarterly
    side_mode: str  # both, long_only, short_only
    lookback_months: int
    validation_months: int
    min_train_trades: int
    min_val_trades: int
    data_provider: str
    db_path: str = "orb_research.db"


def should_reselect(last_asof_date: str | None, today: date, frequency: str) -> bool:
    if last_asof_date is None:
        return True
    prev = pd.Timestamp(last_asof_date).date()
    if frequency == "monthly":
        return (today.year, today.month) != (prev.year, prev.month)
    if frequency == "quarterly":
        prev_q = (prev.month - 1) // 3
        today_q = (today.month - 1) // 3
        return (today.year, today_q) != (prev.year, prev_q)
    raise ValueError(f"Unsupported frequency: {frequency}")


class StrategyReselector:
    def __init__(self, config: SelectionConfig):
        self.config = config
        self.db = Database(Path(config.db_path))

    def run(self) -> dict:
        asof = pd.Timestamp(self.config.asof_date).date()
        eval_end = asof - timedelta(days=1)
        eval_start = (pd.Timestamp(eval_end) - pd.DateOffset(months=self.config.lookback_months)).date()
        val_start = (pd.Timestamp(eval_end) - pd.DateOffset(months=self.config.validation_months) + pd.DateOffset(days=1)).date()
        val_end = eval_end
        train_start = eval_start
        train_end = val_start - timedelta(days=1)

        created_at = datetime.now(timezone.utc).isoformat()
        selected_rows: list[dict] = []
        symbol_reports: list[dict] = []

        for symbol in self.config.symbols:
            bars, provider = load_5m_data(
                symbol=symbol,
                start=train_start.isoformat(),
                end=val_end.isoformat(),
                provider=self.config.data_provider,
            )
            if bars.empty:
                symbol_reports.append(
                    {
                        "symbol": symbol,
                        "status": "no_data",
                        "provider": provider,
                        "selected_strategy": None,
                    }
                )
                continue

            run_id = f"select-{uuid.uuid4()}"
            trades, _, _ = run_orb_backtest(symbol=symbol, bars_5m=bars, run_id=run_id)
            if trades.empty:
                symbol_reports.append(
                    {
                        "symbol": symbol,
                        "status": "no_trades",
                        "provider": provider,
                        "selected_strategy": None,
                    }
                )
                continue

            trades = self._filter_side(trades)
            if trades.empty:
                symbol_reports.append(
                    {
                        "symbol": symbol,
                        "status": "no_trades_after_side_filter",
                        "provider": provider,
                        "selected_strategy": None,
                    }
                )
                continue

            scored = self._score_strategies(
                trades=trades,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
            )
            if scored.empty:
                symbol_reports.append(
                    {
                        "symbol": symbol,
                        "status": "no_scored_strategies",
                        "provider": provider,
                        "selected_strategy": None,
                    }
                )
                continue

            best = scored.sort_values("rank_score", ascending=False).iloc[0].to_dict()
            symbol_reports.append(
                {
                    "symbol": symbol,
                    "status": "selected",
                    "provider": provider,
                    "selected_strategy": best["strategy_id"],
                    "train_return_pct": float(best["train_return_pct"]),
                    "val_return_pct": float(best["val_return_pct"]),
                    "train_trades": int(best["train_trades"]),
                    "val_trades": int(best["val_trades"]),
                    "rank_score": float(best["rank_score"]),
                }
            )

            selected_rows.append(
                {
                    "selection_id": str(uuid.uuid4()),
                    "created_at": created_at,
                    "asof_date": self.config.asof_date,
                    "frequency": self.config.frequency,
                    "side_mode": self.config.side_mode,
                    "symbol": symbol,
                    "strategy_id": best["strategy_id"],
                    "lookback_months": int(self.config.lookback_months),
                    "validation_months": int(self.config.validation_months),
                    "train_start_date": train_start.isoformat(),
                    "train_end_date": train_end.isoformat(),
                    "val_start_date": val_start.isoformat(),
                    "val_end_date": val_end.isoformat(),
                    "train_trades": int(best["train_trades"]),
                    "val_trades": int(best["val_trades"]),
                    "train_return_pct": float(best["train_return_pct"]),
                    "val_return_pct": float(best["val_return_pct"]),
                    "train_pf": float(best["train_pf"]),
                    "val_pf": float(best["val_pf"]),
                    "train_avg_r": float(best["train_avg_r"]),
                    "val_avg_r": float(best["val_avg_r"]),
                    "rank_score": float(best["rank_score"]),
                    "is_active": 1,
                }
            )

        self._persist_selected_rows(selected_rows)
        summary = {
            "asof_date": self.config.asof_date,
            "frequency": self.config.frequency,
            "side_mode": self.config.side_mode,
            "symbols_requested": len(self.config.symbols),
            "symbols_selected": len(selected_rows),
            "symbols": symbol_reports,
        }
        out = Path("strategy_reselection_summary.json")
        out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        return summary

    def fetch_active_map(self, asof_date: str | None = None) -> dict[str, str]:
        if asof_date is None:
            asof_date = pd.Timestamp.now(tz="UTC").date().isoformat()
        query = """
            SELECT symbol, strategy_id
            FROM strategy_selections
            WHERE is_active = 1
              AND frequency = ?
              AND side_mode = ?
              AND asof_date <= ?
            QUALIFY_ROW_NUMBER
        """
        # SQLite has no QUALIFY; use subquery for latest selection per symbol.
        query = """
            SELECT s.symbol, s.strategy_id
            FROM strategy_selections s
            JOIN (
                SELECT symbol, MAX(asof_date) AS max_asof
                FROM strategy_selections
                WHERE is_active = 1
                  AND frequency = ?
                  AND side_mode = ?
                  AND asof_date <= ?
                GROUP BY symbol
            ) x
            ON s.symbol = x.symbol AND s.asof_date = x.max_asof
            WHERE s.is_active = 1
              AND s.frequency = ?
              AND s.side_mode = ?
        """
        df = self.db.query_df(
            query,
            (
                self.config.frequency,
                self.config.side_mode,
                asof_date,
                self.config.frequency,
                self.config.side_mode,
            ),
        )
        if df.empty:
            return {}
        return {row["symbol"]: row["strategy_id"] for _, row in df.iterrows()}

    def latest_active_asof_date(self) -> str | None:
        df = self.db.query_df(
            """
            SELECT MAX(asof_date) AS asof_date
            FROM strategy_selections
            WHERE is_active = 1
              AND frequency = ?
              AND side_mode = ?
            """,
            (self.config.frequency, self.config.side_mode),
        )
        if df.empty:
            return None
        value = df.iloc[0]["asof_date"]
        return str(value) if pd.notna(value) else None

    def _persist_selected_rows(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE strategy_selections
                SET is_active = 0
                WHERE frequency = ? AND side_mode = ?
                """,
                (self.config.frequency, self.config.side_mode),
            )
            pd.DataFrame(rows).to_sql("strategy_selections", conn, if_exists="append", index=False, method="multi", chunksize=200)

    def _filter_side(self, trades: pd.DataFrame) -> pd.DataFrame:
        mode = self.config.side_mode
        if mode == "both":
            return trades
        if mode == "long_only":
            return trades[trades["side"] == "LONG"].copy()
        if mode == "short_only":
            return trades[trades["side"] == "SHORT"].copy()
        raise ValueError(f"Unsupported side mode: {mode}")

    def _score_strategies(
        self,
        trades: pd.DataFrame,
        train_start: date,
        train_end: date,
        val_start: date,
        val_end: date,
    ) -> pd.DataFrame:
        work = trades.copy()
        work["exit_dt"] = pd.to_datetime(work["exit_ts"], utc=True).dt.tz_convert("America/Chicago").dt.date
        train = work[(work["exit_dt"] >= train_start) & (work["exit_dt"] <= train_end)].copy()
        val = work[(work["exit_dt"] >= val_start) & (work["exit_dt"] <= val_end)].copy()

        rows: list[dict] = []
        strategy_ids = sorted(work["strategy_id"].unique().tolist())
        for strategy_id in strategy_ids:
            tr = train[train["strategy_id"] == strategy_id]
            va = val[val["strategy_id"] == strategy_id]
            train_m = self._aggregate(tr)
            val_m = self._aggregate(va)
            spec: StrategySpec = parse_strategy_id(strategy_id)

            eligible = train_m["trades"] >= self.config.min_train_trades and val_m["trades"] >= self.config.min_val_trades
            strict = (
                eligible
                and train_m["ret_pct"] > 0
                and val_m["ret_pct"] > 0
                and train_m["pf"] >= 1.0
                and val_m["pf"] >= 1.0
                and train_m["avg_r"] > 0
                and val_m["avg_r"] > 0
            )

            stability = min(train_m["ret_pct"], val_m["ret_pct"])
            val_pf_score = val_m["pf"] if math.isfinite(val_m["pf"]) else 10.0
            score = (
                (1_000_000.0 if strict else 0.0)
                + (10_000.0 if eligible else 0.0)
                + (stability * 10.0)
                + (val_m["ret_pct"] * 5.0)
                + val_pf_score
                + val_m["avg_r"]
            )

            rows.append(
                {
                    "strategy_id": strategy_id,
                    "timeframe_min": spec.timeframe_min,
                    "exit_variant": spec.exit_variant,
                    "trade_limit_1d": spec.trade_limit_1d,
                    "long_cutoff_ct": spec.long_cutoff.strftime("%H:%M") if spec.long_cutoff else "NONE",
                    "eligible": int(eligible),
                    "strict": int(strict),
                    "train_trades": train_m["trades"],
                    "val_trades": val_m["trades"],
                    "train_return_pct": train_m["ret_pct"],
                    "val_return_pct": val_m["ret_pct"],
                    "train_pf": train_m["pf"],
                    "val_pf": val_m["pf"],
                    "train_avg_r": train_m["avg_r"],
                    "val_avg_r": val_m["avg_r"],
                    "stability_min_ret": stability,
                    "rank_score": score,
                }
            )

        out = pd.DataFrame(rows)
        out = out.sort_values("rank_score", ascending=False).reset_index(drop=True)
        return out

    @staticmethod
    def _aggregate(df: pd.DataFrame) -> dict:
        if df.empty:
            return {"trades": 0, "ret_pct": 0.0, "pf": 0.0, "avg_r": 0.0}
        pnl = df["pnl"].astype(float)
        gp = float(pnl[pnl > 0].sum())
        gl = abs(float(pnl[pnl < 0].sum()))
        if gl > 0:
            pf = gp / gl
        elif gp > 0:
            pf = float("inf")
        else:
            pf = 0.0
        ret = (float((1.0 + df["ret_pct"].astype(float)).prod()) - 1.0) * 100.0
        avg_r = float(df["r_mult"].astype(float).mean())
        return {"trades": int(len(df)), "ret_pct": ret, "pf": pf, "avg_r": avg_r}
