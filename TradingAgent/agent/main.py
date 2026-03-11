from __future__ import annotations

import argparse
import json

from agent.config import LiveConfig, OrbConfig, ReselectConfig
from agent.live_orb import LiveOrbTrader, LiveTradeConfig
from agent.live_orb_session import LiveOrbSessionConfig, LiveOrbSessionRunner
from agent.orb_research import OrbResearchEngine
from agent.paper_profile import load_paper_profile
from agent.selection import SelectionConfig, StrategyReselector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ORB breakout research runner")
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "orb",
            "orb_fold_report",
            "orb_gap_fold_eval",
            "regime_lock_router_eval",
            "reselect",
            "trade",
            "paper",
            "paper_live",
            "r6_run",
            "r6_replay",
            "r6_live",
            "r6_research",
            "r6_paper",
            "ema20_research",
            "ema20_walkforward",
            "ema20_rolling",
        ],
        help="Execution mode",
    )
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--symbol", default="SPY", help="Ticker symbol for --mode orb")
    parser.add_argument("--symbols", default="SPY", help="Comma-separated symbols for reselect/trade")
    parser.add_argument("--asof", help="Asof date YYYY-MM-DD (defaults to today in CT for trade)")
    parser.add_argument("--profile", default="paper_profile.json", help="Paper profile JSON path for --mode paper")
    parser.add_argument("--frequency", choices=["monthly", "quarterly"], default="monthly")
    parser.add_argument("--side-mode", choices=["both", "long_only", "short_only"], default="long_only")
    parser.add_argument("--lookback-months", type=int, default=18)
    parser.add_argument("--validation-months", type=int, default=6)
    parser.add_argument("--min-train-trades", type=int, default=30)
    parser.add_argument("--min-val-trades", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true", help="Run trading mode without placing broker orders")
    parser.add_argument("--force-reselect", action="store_true", help="Force strategy reselection before trading")
    parser.add_argument("--risk-pct", type=float, default=0.005, help="Risk per trade as fraction of equity")
    parser.add_argument("--max-notional-pct", type=float, default=0.20, help="Max notional allocation per trade")
    parser.add_argument("--max-notional-dollars", type=float, default=5000.0, help="Absolute notional cap per trade")
    parser.add_argument("--max-open-positions", type=int, default=8)
    parser.add_argument("--default-equity", type=float, default=100000.0, help="Used only in dry-run mode")
    parser.add_argument("--default-strategy-id", default="TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE")
    parser.add_argument("--db-path", default="orb_research.db", help="SQLite db path")
    parser.add_argument("--data-provider", choices=["auto", "alpaca", "yahoo", "synthetic"], default="auto")
    parser.add_argument("--selection-data-provider", choices=["auto", "alpaca", "yahoo", "synthetic"], default="alpaca")
    parser.add_argument("--orb-run-id", default=None, help="Run id for orb_fold_report mode (optional)")
    parser.add_argument("--orb-locked-strategy-id", default="TF15_STACK_TSNP_LIMIT1_LONG_CUTOFF_NONE", help="Locked strategy id for orb_fold_report mode")
    parser.add_argument("--orb-min-train-trades", type=int, default=200, help="Minimum train trades gate for orb_fold_report")
    parser.add_argument("--orb-output-csv", default="./artifacts/orb_research/orb_yearly_folds.csv", help="Output CSV for orb_fold_report mode")
    parser.add_argument("--gap-yearly-csv", default="./orb_gap15_universe_yearly.csv", help="Input yearly CSV for orb_gap_fold_eval mode")
    parser.add_argument("--gap-output-csv", default="./artifacts/reports/orb_gap_fold_decision.csv", help="Output detail CSV for orb_gap_fold_eval mode")
    parser.add_argument("--gap-output-json", default="./artifacts/reports/orb_gap_fold_summary.json", help="Output summary JSON for orb_gap_fold_eval mode")
    parser.add_argument("--gap-base-model", default="base_limit1", help="Baseline model name in yearly CSV for orb_gap_fold_eval")
    parser.add_argument("--gap-combo-model", default="combo", help="Gap-combo model name in yearly CSV for orb_gap_fold_eval")
    parser.add_argument("--regime-orb-db", default="./orb_research.db", help="ORB DB path for regime_lock_router_eval")
    parser.add_argument("--regime-r6-db", default="./artifacts/orb_r6/orb_core.sqlite", help="R6 DB path for regime_lock_router_eval")
    parser.add_argument("--regime-output-csv", default="./artifacts/reports/regime_lock_router_fold_results.csv", help="Output fold CSV for regime_lock_router_eval")
    parser.add_argument("--regime-output-json", default="./artifacts/reports/regime_lock_router_summary.json", help="Output summary JSON for regime_lock_router_eval")
    parser.add_argument("--regime-output-trades-csv", default="./artifacts/reports/regime_lock_router_trade_details.csv", help="Output per-trade details CSV for regime_lock_router_eval")
    parser.add_argument("--r6-config", default="orb_r6_config.yaml", help="Config path for r6_* modes")
    parser.add_argument("--ema20-config", default="ema20_stable/config.research.yaml", help="Config path for ema20_* modes")
    parser.add_argument("--ema20-train-months", type=int, default=18, help="Train window (months) for ema20_rolling")
    parser.add_argument("--ema20-validate-months", type=int, default=6, help="Validate window (months) for ema20_rolling")
    parser.add_argument("--ema20-test-months", type=int, default=3, help="Test window (months) for ema20_rolling")
    parser.add_argument("--ema20-step-months", type=int, default=6, help="Fold step size (months) for ema20_rolling")
    parser.add_argument("--ema20-min-test-pf", type=float, default=1.2, help="Minimum test PF gate for ema20_rolling")
    parser.add_argument("--ema20-max-test-dd", type=float, default=30.0, help="Maximum test drawdown pct gate for ema20_rolling")
    parser.add_argument("--ema20-min-test-trades", type=int, default=5, help="Minimum test trades gate for ema20_rolling")
    parser.add_argument(
        "--ema20-min-test-excess-vs-bh",
        type=float,
        default=0.0,
        help="Minimum test excess return pct vs equal-weight buyhold gate for ema20_rolling",
    )
    parser.add_argument("--poll-seconds", type=int, default=None, help="Polling interval for paper_live mode")
    parser.add_argument("--session-calendar", default=None, help="Exchange calendar for paper_live mode (default from profile)")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable terminal dashboard for paper_live mode")
    parser.add_argument("--no-wait-for-open", action="store_true", help="Do not wait for session open in paper_live mode")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "orb":
        if not args.start or not args.end:
            raise ValueError("--start and --end are required for --mode orb")
        config = OrbConfig(
            symbol=args.symbol.upper(),
            start_date=args.start,
            end_date=args.end,
            db_path=args.db_path,
            data_provider=args.data_provider,
            mode=args.mode,
        )
        summary = OrbResearchEngine(config).run()
    elif args.mode == "orb_fold_report":
        from agent.orb_fold_report import build_fold_report, resolve_run_id

        run_id = resolve_run_id(db_path=args.db_path, run_id=args.orb_run_id, symbol=args.symbol)
        out = build_fold_report(
            db_path=args.db_path,
            run_id=run_id,
            locked_strategy_id=args.orb_locked_strategy_id,
            output_csv=args.orb_output_csv,
            min_train_trades=args.orb_min_train_trades,
        )
        summary = {
            "status": "completed",
            "mode": "orb_fold_report",
            "db_path": args.db_path,
            "run_id": run_id,
            "locked_strategy_id": args.orb_locked_strategy_id,
            "output_csv": args.orb_output_csv,
            "rows": int(len(out)),
        }
    elif args.mode == "orb_gap_fold_eval":
        from agent.orb_gap_fold_eval import build_gap_fold_report

        detail, gap_summary = build_gap_fold_report(
            yearly_csv=args.gap_yearly_csv,
            output_csv=args.gap_output_csv,
            output_json=args.gap_output_json,
            base_model=args.gap_base_model,
            combo_model=args.gap_combo_model,
        )
        summary = {
            "status": "completed",
            "mode": "orb_gap_fold_eval",
            "yearly_csv": args.gap_yearly_csv,
            "output_csv": args.gap_output_csv,
            "output_json": args.gap_output_json,
            "rows": int(len(detail)),
            "selected_minus_base_mean": float(gap_summary["equal_weight"]["all"]["selected_minus_base_mean"]),
            "combo_minus_base_mean": float(gap_summary["equal_weight"]["all"]["combo_minus_base_mean"]),
        }
    elif args.mode == "regime_lock_router_eval":
        from agent.regime_lock_router_eval import run as run_regime_eval

        run_regime_eval(
            orb_db=args.regime_orb_db,
            r6_db=args.regime_r6_db,
            output_csv=args.regime_output_csv,
            output_json=args.regime_output_json,
            output_trades_csv=args.regime_output_trades_csv,
        )
        summary = {
            "status": "completed",
            "mode": "regime_lock_router_eval",
            "orb_db": args.regime_orb_db,
            "r6_db": args.regime_r6_db,
            "output_csv": args.regime_output_csv,
            "output_json": args.regime_output_json,
            "output_trades_csv": args.regime_output_trades_csv,
        }
    elif args.mode == "reselect":
        if not args.asof:
            raise ValueError("--asof is required for --mode reselect")
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        config = ReselectConfig(
            symbols=symbols,
            asof_date=args.asof,
            frequency=args.frequency,
            side_mode=args.side_mode,
            lookback_months=args.lookback_months,
            validation_months=args.validation_months,
            min_train_trades=args.min_train_trades,
            min_val_trades=args.min_val_trades,
            data_provider=args.data_provider,
            db_path=args.db_path,
        )
        summary = StrategyReselector(
            SelectionConfig(
                symbols=config.symbols,
                asof_date=config.asof_date,
                frequency=config.frequency,
                side_mode=config.side_mode,
                lookback_months=config.lookback_months,
                validation_months=config.validation_months,
                min_train_trades=config.min_train_trades,
                min_val_trades=config.min_val_trades,
                data_provider=config.data_provider,
                db_path=config.db_path,
            )
        ).run()
    elif args.mode == "trade":
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        config = LiveConfig(
            symbols=symbols,
            asof_date=args.asof,
            frequency=args.frequency,
            side_mode=args.side_mode,
            lookback_months=args.lookback_months,
            validation_months=args.validation_months,
            min_train_trades=args.min_train_trades,
            min_val_trades=args.min_val_trades,
            data_provider=args.data_provider,
            selection_data_provider=args.selection_data_provider,
            db_path=args.db_path,
            dry_run=args.dry_run,
            risk_pct_per_trade=args.risk_pct,
            max_notional_pct=args.max_notional_pct,
            max_open_positions=args.max_open_positions,
            default_equity=args.default_equity,
            force_reselect=args.force_reselect,
            default_strategy_id=args.default_strategy_id,
        )
        summary = LiveOrbTrader(
            LiveTradeConfig(
                symbols=config.symbols,
                asof_date=config.asof_date,
                frequency=config.frequency,
                side_mode=config.side_mode,
                lookback_months=config.lookback_months,
                validation_months=config.validation_months,
                min_train_trades=config.min_train_trades,
                min_val_trades=config.min_val_trades,
                data_provider=config.data_provider,
                selection_data_provider=config.selection_data_provider,
                db_path=config.db_path,
                dry_run=config.dry_run,
                risk_pct_per_trade=config.risk_pct_per_trade,
                max_notional_pct=config.max_notional_pct,
                max_notional_dollars=args.max_notional_dollars,
                max_open_positions=config.max_open_positions,
                default_equity=config.default_equity,
                force_reselect=config.force_reselect,
                default_strategy_id=config.default_strategy_id,
                discord_enabled=False,
                discord_webhook_url="",
                short_requires_inventory=True,
                gap_entry_enabled=config.gap_entry_enabled,
                gap_entry_timeframe_min=config.gap_entry_timeframe_min,
                gap_entry_apply_on_limit1=config.gap_entry_apply_on_limit1,
                gap_entry_gap_threshold=config.gap_entry_gap_threshold,
                gap_entry_ema_dist_min=config.gap_entry_ema_dist_min,
                gap_entry_ema_dist_max=config.gap_entry_ema_dist_max,
                gap_entry_require_close_compare=config.gap_entry_require_close_compare,
                gap_entry_require_body_direction=config.gap_entry_require_body_direction,
                live_entry_max_age_bars=config.live_entry_max_age_bars,
            )
        ).run()
    elif args.mode == "paper":
        profile = load_paper_profile(args.profile)
        symbols = profile.symbols
        if args.symbols and args.symbols.strip().upper() != "SPY":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        live_provider = profile.live_data_provider
        if args.data_provider != "auto":
            live_provider = args.data_provider
        summary = LiveOrbTrader(
            LiveTradeConfig(
                symbols=symbols,
                asof_date=args.asof,
                frequency=profile.frequency,
                side_mode=profile.side_mode,
                lookback_months=profile.lookback_months,
                validation_months=profile.validation_months,
                min_train_trades=profile.min_train_trades,
                min_val_trades=profile.min_val_trades,
                data_provider=live_provider,
                selection_data_provider=profile.selection_data_provider,
                db_path=profile.db_path,
                dry_run=args.dry_run,
                risk_pct_per_trade=profile.risk_pct_per_trade,
                max_notional_pct=profile.max_notional_pct,
                max_notional_dollars=profile.max_notional_dollars,
                max_open_positions=profile.max_open_positions,
                default_equity=profile.default_equity,
                force_reselect=args.force_reselect,
                default_strategy_id=profile.default_strategy_id,
                discord_enabled=profile.discord_enabled,
                discord_webhook_url=profile.discord_webhook_url,
                short_requires_inventory=profile.short_requires_inventory,
                gap_entry_enabled=profile.gap_entry_enabled,
                gap_entry_timeframe_min=profile.gap_entry_timeframe_min,
                gap_entry_apply_on_limit1=profile.gap_entry_apply_on_limit1,
                gap_entry_gap_threshold=profile.gap_entry_gap_threshold,
                gap_entry_ema_dist_min=profile.gap_entry_ema_dist_min,
                gap_entry_ema_dist_max=profile.gap_entry_ema_dist_max,
                gap_entry_require_close_compare=profile.gap_entry_require_close_compare,
                gap_entry_require_body_direction=profile.gap_entry_require_body_direction,
                live_entry_max_age_bars=profile.live_entry_max_age_bars,
            )
        ).run()
    elif args.mode == "paper_live":
        profile = load_paper_profile(args.profile)
        symbols = profile.symbols
        if args.symbols and args.symbols.strip().upper() != "SPY":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        live_provider = profile.live_data_provider
        if args.data_provider != "auto":
            live_provider = args.data_provider
        trade_config = LiveTradeConfig(
            symbols=symbols,
            asof_date=args.asof,
            frequency=profile.frequency,
            side_mode=profile.side_mode,
            lookback_months=profile.lookback_months,
            validation_months=profile.validation_months,
            min_train_trades=profile.min_train_trades,
            min_val_trades=profile.min_val_trades,
            data_provider=live_provider,
            selection_data_provider=profile.selection_data_provider,
            db_path=profile.db_path,
            dry_run=args.dry_run,
            risk_pct_per_trade=profile.risk_pct_per_trade,
            max_notional_pct=profile.max_notional_pct,
            max_notional_dollars=profile.max_notional_dollars,
            max_open_positions=profile.max_open_positions,
            default_equity=profile.default_equity,
            force_reselect=args.force_reselect,
            default_strategy_id=profile.default_strategy_id,
            discord_enabled=profile.discord_enabled,
            discord_webhook_url=profile.discord_webhook_url,
            short_requires_inventory=profile.short_requires_inventory,
            gap_entry_enabled=profile.gap_entry_enabled,
            gap_entry_timeframe_min=profile.gap_entry_timeframe_min,
            gap_entry_apply_on_limit1=profile.gap_entry_apply_on_limit1,
            gap_entry_gap_threshold=profile.gap_entry_gap_threshold,
            gap_entry_ema_dist_min=profile.gap_entry_ema_dist_min,
            gap_entry_ema_dist_max=profile.gap_entry_ema_dist_max,
            gap_entry_require_close_compare=profile.gap_entry_require_close_compare,
            gap_entry_require_body_direction=profile.gap_entry_require_body_direction,
            live_entry_max_age_bars=profile.live_entry_max_age_bars,
        )
        summary = LiveOrbSessionRunner(
            LiveOrbSessionConfig(
                trade_config=trade_config,
                session_calendar=args.session_calendar or profile.live_session_calendar,
                poll_seconds=args.poll_seconds or profile.live_poll_seconds,
                wait_for_open=(False if args.no_wait_for_open else profile.live_wait_for_open),
                dashboard=(False if args.no_dashboard else profile.live_dashboard),
                dashboard_min_refresh_seconds=profile.live_dashboard_min_refresh_seconds,
            )
        ).run()
    elif args.mode == "r6_run":
        from agent.orb_r6.run import run as r6_run

        r6_run(args.r6_config)
        summary = {"status": "completed", "mode": "r6_run", "config_path": args.r6_config}
    elif args.mode == "r6_research":
        if not args.start or not args.end:
            raise ValueError("--start and --end are required for --mode r6_research")
        from agent.orb_r6.research import ResearchConfig, run_research

        symbols = None
        if args.symbols and args.symbols.strip().upper() != "SPY":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        summary = run_research(
            ResearchConfig(
                config_path=args.r6_config,
                start_date=args.start,
                end_date=args.end,
                symbols=symbols,
            )
        )
    elif args.mode == "r6_replay":
        from agent.orb_r6.replay import run as r6_replay

        r6_replay(args.r6_config)
        summary = {"status": "completed", "mode": "r6_replay", "config_path": args.r6_config}
    elif args.mode == "ema20_research":
        if not args.start or not args.end:
            raise ValueError("--start and --end are required for --mode ema20_research")
        from agent.ema20.research import ResearchConfig, run_research

        symbols = None
        if args.symbols and args.symbols.strip().upper() != "SPY":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        summary = run_research(
            ResearchConfig(
                config_path=args.ema20_config,
                start_date=args.start,
                end_date=args.end,
                symbols=symbols,
            )
        )
    elif args.mode == "ema20_walkforward":
        from agent.ema20.walkforward import WalkForwardConfig, run_walkforward

        symbols = None
        if args.symbols and args.symbols.strip().upper() != "SPY":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        summary = run_walkforward(
            WalkForwardConfig(
                config_path=args.ema20_config,
                test_end=(args.end if args.end else "2026-02-23"),
                symbols=symbols,
            )
        )
    elif args.mode == "ema20_rolling":
        from agent.ema20.rolling import RollingWalkForwardConfig, run_rolling_walkforward

        symbols = None
        if args.symbols and args.symbols.strip().upper() != "SPY":
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        summary = run_rolling_walkforward(
            RollingWalkForwardConfig(
                config_path=args.ema20_config,
                start_date=(args.start if args.start else "2023-01-03"),
                end_date=(args.end if args.end else "2026-02-23"),
                train_months=args.ema20_train_months,
                validate_months=args.ema20_validate_months,
                test_months=args.ema20_test_months,
                step_months=args.ema20_step_months,
                min_test_pf=args.ema20_min_test_pf,
                max_test_dd_pct=args.ema20_max_test_dd,
                min_test_trades=args.ema20_min_test_trades,
                min_test_excess_vs_buyhold_pct=args.ema20_min_test_excess_vs_bh,
                symbols=symbols,
            )
        )
    elif args.mode == "r6_live":
        from agent.orb_r6.live import run as r6_live

        r6_live(args.r6_config)
        summary = {"status": "completed", "mode": "r6_live", "config_path": args.r6_config}
    else:
        from agent.orb_r6.paper import run as r6_paper

        r6_paper(args.r6_config)
        summary = {"status": "completed", "mode": "r6_paper", "config_path": args.r6_config}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
