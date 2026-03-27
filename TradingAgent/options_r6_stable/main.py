from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict

from .alpaca_stage import (
    stage_alpaca_event,
    stage_alpaca_existing_signals,
    stage_historical_event,
    stage_historical_existing_signals,
)
from .capture import record_decision_capture
from .config_loader import load_config
from .contract_selector import select_contract
from .db import connect, init_db
from .doctor import run_doctor
from .exit_experiments import run_exit_experiments, run_protocol_sweep
from .models import OptionContractSnapshot, PortfolioState, UnderlyingSignal
from .research import run_research_replay, seed_sample_research_inputs
from .r6_seed import seed_r6_signals
from .strategy import build_trade_plan
from .symbols import load_symbols


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Options R6 Stable scaffold CLI")
    parser.add_argument("--config", default="options_r6_stable/config/config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("describe-config")
    sub.add_parser("doctor")
    sub.add_parser("init-db")

    demo = sub.add_parser("plan-demo")
    demo.add_argument("--chain-csv", required=True)
    demo.add_argument("--symbol", required=True)
    demo.add_argument("--direction", choices=["BULLISH", "BEARISH"], required=True)
    demo.add_argument("--event-ts", required=True)
    demo.add_argument("--equity", type=float, default=100000.0)
    demo.add_argument("--underlying-price", type=float, default=None)
    demo.add_argument("--underlying-stop", type=float, default=None)
    demo.add_argument("--confidence", type=float, default=None)
    demo.add_argument("--cash-available", type=float, default=None)
    demo.add_argument("--open-premium-total", type=float, default=0.0)
    demo.add_argument("--open-symbol-premium", type=float, default=0.0)
    demo.add_argument("--open-direction-premium", type=float, default=0.0)
    demo.add_argument("--realized-pnl-today", type=float, default=0.0)
    demo.add_argument("--new-trades-today", type=int, default=0)

    record = sub.add_parser("record-demo")
    record.add_argument("--chain-csv", required=True)
    record.add_argument("--symbol", required=True)
    record.add_argument("--direction", choices=["BULLISH", "BEARISH"], required=True)
    record.add_argument("--event-ts", required=True)
    record.add_argument("--equity", type=float, default=100000.0)
    record.add_argument("--underlying-price", type=float, default=None)
    record.add_argument("--underlying-stop", type=float, default=None)
    record.add_argument("--confidence", type=float, default=None)
    record.add_argument("--source-provider", default="sample_csv")
    record.add_argument("--cash-available", type=float, default=None)
    record.add_argument("--open-premium-total", type=float, default=0.0)
    record.add_argument("--open-symbol-premium", type=float, default=0.0)
    record.add_argument("--open-direction-premium", type=float, default=0.0)
    record.add_argument("--realized-pnl-today", type=float, default=0.0)
    record.add_argument("--new-trades-today", type=int, default=0)

    sub.add_parser("seed-sample-research")

    stage_event = sub.add_parser("stage-alpaca-event")
    stage_event.add_argument("--symbol", required=True)
    stage_event.add_argument("--direction", choices=["BULLISH", "BEARISH"], required=True)
    stage_event.add_argument("--event-ts", required=True)
    stage_event.add_argument("--underlying-stop", type=float, default=None)
    stage_event.add_argument("--underlying-price", type=float, default=None)
    stage_event.add_argument("--confidence", type=float, default=None)
    stage_event.add_argument("--variant-id", default=None)
    stage_event.add_argument("--ref-horizon", type=int, default=None)
    stage_event.add_argument("--include-today-or", type=int, default=0)

    stage_signals = sub.add_parser("stage-alpaca-signals")
    stage_signals.add_argument("--start", default=None)
    stage_signals.add_argument("--end", default=None)
    stage_signals.add_argument("--limit", type=int, default=None)

    stage_hist_event = sub.add_parser("stage-historical-event")
    stage_hist_event.add_argument("--symbol", required=True)
    stage_hist_event.add_argument("--direction", choices=["BULLISH", "BEARISH"], required=True)
    stage_hist_event.add_argument("--event-ts", required=True)
    stage_hist_event.add_argument("--underlying-stop", type=float, default=None)
    stage_hist_event.add_argument("--underlying-price", type=float, default=None)
    stage_hist_event.add_argument("--confidence", type=float, default=None)
    stage_hist_event.add_argument("--variant-id", default=None)
    stage_hist_event.add_argument("--ref-horizon", type=int, default=None)
    stage_hist_event.add_argument("--include-today-or", type=int, default=0)

    stage_hist_signals = sub.add_parser("stage-historical-signals")
    stage_hist_signals.add_argument("--start", default=None)
    stage_hist_signals.add_argument("--end", default=None)
    stage_hist_signals.add_argument("--limit", type=int, default=None)

    seed_r6 = sub.add_parser("seed-r6-signals")
    seed_r6.add_argument("--start", required=True)
    seed_r6.add_argument("--end", required=True)
    seed_r6.add_argument("--source-db", default=None)
    seed_r6.add_argument("--variant-id", default=None)

    replay = sub.add_parser("research-replay")
    replay.add_argument("--start", default=None)
    replay.add_argument("--end", default=None)
    replay.add_argument("--starting-equity", type=float, default=100000.0)
    replay.add_argument("--run-label", default=None)

    exp_exit = sub.add_parser("experiment-exits")
    exp_exit.add_argument("--start", required=True)
    exp_exit.add_argument("--end", required=True)
    exp_exit.add_argument("--label", required=True)

    protocol = sub.add_parser("protocol-sweep")
    protocol.add_argument("--start", required=True)
    protocol.add_argument("--end", required=True)
    protocol.add_argument("--label", required=True)
    protocol.add_argument("--starting-equity", type=float, default=100000.0)
    protocol.add_argument("--reveal-blind", action="store_true")
    return parser.parse_args()


def _load_chain_csv(path: str, symbol: str) -> list[OptionContractSnapshot]:
    contracts: list[OptionContractSnapshot] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            underlying_symbol = str((row or {}).get("underlying_symbol") or symbol).strip().upper()
            contracts.append(
                OptionContractSnapshot(
                    option_symbol=str(row["option_symbol"]).strip(),
                    underlying_symbol=underlying_symbol,
                    right=str(row["right"]).strip().lower(),  # type: ignore[arg-type]
                    expiration_date=str(row["expiration_date"]).strip(),
                    strike=float(row["strike"]),
                    dte=int(row["dte"]),
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    delta=(None if row.get("delta") in (None, "", "null", "None") else float(row["delta"])),
                    open_interest=(None if row.get("open_interest") in (None, "", "null", "None") else int(float(row["open_interest"]))),
                    volume=(None if row.get("volume") in (None, "", "null", "None") else int(float(row["volume"]))),
                    last=(None if row.get("last") in (None, "", "null", "None") else float(row["last"])),
                    iv=(None if row.get("iv") in (None, "", "null", "None") else float(row["iv"])),
                    gamma=(None if row.get("gamma") in (None, "", "null", "None") else float(row["gamma"])),
                    theta=(None if row.get("theta") in (None, "", "null", "None") else float(row["theta"])),
                    vega=(None if row.get("vega") in (None, "", "null", "None") else float(row["vega"])),
                )
            )
    return contracts


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)

    if args.command == "describe-config":
        payload = {
            "version": cfg.version,
            "status": cfg.status,
            "db_path": str(cfg.resolved_db_path),
            "reports_dir": str(cfg.resolved_reports_dir),
            "research_output_dir": str(cfg.resolved_research_output_dir),
            "symbols": load_symbols(cfg.symbols),
            "underlying_signal": asdict(cfg.underlying_signal),
            "options": asdict(cfg.options),
            "market_data": asdict(cfg.market_data),
            "research": asdict(cfg.research),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if args.command == "doctor":
        print(json.dumps(run_doctor(args.config), indent=2, sort_keys=True))
        return

    if args.command == "init-db":
        conn = connect(cfg.db.path)
        init_db(conn)
        conn.close()
        print(json.dumps({"status": "initialized", "db_path": str(cfg.resolved_db_path)}, indent=2))
        return

    if args.command == "seed-sample-research":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = seed_sample_research_inputs(conn=conn, cfg=cfg)
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    if args.command == "stage-alpaca-event":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = stage_alpaca_event(
            conn=conn,
            cfg=cfg,
            symbol=args.symbol,
            direction=args.direction,
            event_ts=args.event_ts,
            underlying_stop_price=args.underlying_stop,
            underlying_price=args.underlying_price,
            confidence=args.confidence,
            variant_id=args.variant_id,
            ref_horizon=args.ref_horizon,
            include_today_or=args.include_today_or,
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "stage-historical-event":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = stage_historical_event(
            conn=conn,
            cfg=cfg,
            symbol=args.symbol,
            direction=args.direction,
            event_ts=args.event_ts,
            underlying_stop_price=args.underlying_stop,
            underlying_price=args.underlying_price,
            confidence=args.confidence,
            variant_id=args.variant_id,
            ref_horizon=args.ref_horizon,
            include_today_or=args.include_today_or,
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "stage-alpaca-signals":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = stage_alpaca_existing_signals(
            conn=conn,
            cfg=cfg,
            start=args.start,
            end=args.end,
            limit=args.limit,
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "stage-historical-signals":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = stage_historical_existing_signals(
            conn=conn,
            cfg=cfg,
            start=args.start,
            end=args.end,
            limit=args.limit,
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "seed-r6-signals":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = seed_r6_signals(
            conn=conn,
            cfg=cfg,
            start=args.start,
            end=args.end,
            source_db_path=(args.source_db or "./artifacts/r6_stable/orb_core.sqlite"),
            variant_id=args.variant_id,
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command in {"plan-demo", "record-demo"}:
        portfolio_state = PortfolioState(
            cash_available=args.cash_available,
            open_premium_total=float(args.open_premium_total),
            open_symbol_premium=float(args.open_symbol_premium),
            open_direction_premium=float(args.open_direction_premium),
            realized_pnl_today=float(args.realized_pnl_today),
            new_trades_today=int(args.new_trades_today),
        )
        signal = UnderlyingSignal(
            symbol=str(args.symbol).strip().upper(),
            direction=str(args.direction).strip().upper(),  # type: ignore[arg-type]
            event_ts=str(args.event_ts).strip(),
            variant_id=cfg.underlying_signal.variant_id,
            confidence=args.confidence,
            underlying_price=args.underlying_price,
            underlying_stop_price=args.underlying_stop,
        )
        chain = _load_chain_csv(args.chain_csv, signal.symbol)
        evaluated, selected = select_contract(signal=signal, contracts=chain, cfg=cfg.options)
        plan = build_trade_plan(
            signal=signal,
            chain=chain,
            account_equity=float(args.equity),
            cfg=cfg.options,
            portfolio_state=portfolio_state,
        )
        payload = {
            "signal": asdict(signal),
            "portfolio_state": asdict(portfolio_state),
            "contracts_loaded": len(chain),
            "selected_contract": (None if selected is None else asdict(selected.contract)),
            "selection_reason": (None if selected is None else selected.selection_reason),
            "evaluated": [
                {
                    "option_symbol": r.contract.option_symbol,
                    "passed": r.passed,
                    "reject_reason": r.reject_reason,
                    "score": list(r.score),
                    "score_details": r.score_details,
                    "filter_flags": r.filter_flags,
                    "dte": r.contract.dte,
                    "delta": r.contract.delta,
                    "spread_pct": r.contract.spread_pct,
                    "open_interest": r.contract.open_interest,
                    "volume": r.contract.volume,
                }
                for r in evaluated
            ],
            "trade_plan": asdict(plan),
        }
        if args.command == "record-demo":
            conn = connect(cfg.db.path)
            init_db(conn)
            capture = record_decision_capture(
                conn=conn,
                cfg=cfg,
                signal=signal,
                chain=chain,
                account_equity=float(args.equity),
                source_provider=str(args.source_provider).strip(),
                mode="record_demo",
                portfolio_state=portfolio_state,
            )
            conn.close()
            payload["capture"] = capture
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "research-replay":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = run_research_replay(
            conn=conn,
            cfg=cfg,
            start=args.start,
            end=args.end,
            starting_equity=float(args.starting_equity),
            run_label=(None if args.run_label in (None, "") else str(args.run_label)),
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "experiment-exits":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = run_exit_experiments(
            conn=conn,
            cfg=cfg,
            start=args.start,
            end=args.end,
            output_label=args.label,
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if args.command == "protocol-sweep":
        conn = connect(cfg.db.path)
        init_db(conn)
        payload = run_protocol_sweep(
            conn=conn,
            cfg=cfg,
            start=args.start,
            end=args.end,
            output_label=args.label,
            starting_equity=float(args.starting_equity),
            reveal_blind=bool(args.reveal_blind),
        )
        conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    raise RuntimeError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
