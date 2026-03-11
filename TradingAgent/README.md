# ORB Research + Execution Agent

Production-oriented 30-minute Opening Range Breakout engine with:
- research matrix backtesting (`--mode orb`)
- monthly/quarterly strategy reselection using prior data only (`--mode reselect`)
- live/paper execution loop with managed exits (`--mode trade`)
- simple paper profile mode (`--mode paper`)
- ORB_R_6 parity pipeline (`--mode r6_run`, `--mode r6_replay`, `--mode r6_live`, `--mode r6_paper`)
- ORB_R_6 research backtest layer (`--mode r6_research`)

## Strategy Core
- Timezone: `America/Chicago`
- Session: `08:30` to `15:00`
- Opening range: `08:30`-`09:00`
- Entry confirmation:
  - LONG: 2 consecutive closes above OR high
  - SHORT: 2 consecutive closes below OR low
- Entry at confirmation bar close

## Research Mode
Run full matrix over historical bars:

```bash
python -m agent.main --mode orb --start 2025-01-01 --end 2025-12-31 --symbol SPY --data-provider alpaca
```

Artifacts:
- `orb_summary.json`
- `orb_experiment_metrics.csv`
- `orb_yearly_returns.csv`
- `orb_constraint_comparison.csv`
- `orb_side_performance.csv`
- `orb_trades.csv`
- `orb_research.db`

## Reselection Mode (Monthly/Quarterly)
Select active strategy map by symbol using strict train/validation split from prior data only.

```bash
python -m agent.main --mode reselect \
  --symbols SPY,AAPL,NVDA,TSLA,MSFT,AMZN,GOOGL,META,V,ADBE,AMD,MA,VGT,VOO,SCHG,VTI \
  --asof 2026-02-23 \
  --frequency monthly \
  --side-mode long_only \
  --lookback-months 18 \
  --validation-months 6 \
  --min-train-trades 30 \
  --min-val-trades 10 \
  --data-provider alpaca
```

Artifacts:
- `strategy_reselection_summary.json`
- DB table: `strategy_selections` (active map + metrics)

## Trade Mode (Paper/Live Loop)
Run this every 5 minutes during market hours (scheduler/cron/task scheduler).
The trader:
- checks if reselection is due (monthly/quarterly), can auto-run it
- loads active strategy map from DB
- evaluates ORB entries
- opens positions with risk-based sizing
- manages exits + protective stop maintenance

Dry-run example:

```bash
python -m agent.main --mode trade \
  --symbols SPY,AAPL,NVDA,TSLA,MSFT,AMZN,GOOGL,META,V,ADBE,AMD,MA,VGT,VOO,SCHG,VTI \
  --asof 2026-02-23 \
  --frequency monthly \
  --side-mode long_only \
  --dry-run \
  --risk-pct 0.005 \
  --max-notional-pct 0.20 \
  --max-open-positions 8 \
  --data-provider alpaca
```

Live paper execution example:

```bash
python -m agent.main --mode trade \
  --symbols SPY,AAPL,NVDA,TSLA,MSFT,AMZN,GOOGL,META,V,ADBE,AMD,MA,VGT,VOO,SCHG,VTI \
  --frequency monthly \
  --side-mode long_only \
  --risk-pct 0.005 \
  --max-notional-pct 0.20 \
  --max-open-positions 8 \
  --data-provider alpaca
```

## Simple Paper Mode (Recommended)
If you want a short command with minimal args:

```bash
python -m agent.main --mode paper
```

This mode reads `paper_profile.json` and uses:
- signal data: Yahoo 5m (`live_data_provider`)
- reselection data: Alpaca historical (`selection_data_provider`)
- order routing: Alpaca paper account

Optional:
- force reselection now: `python -m agent.main --mode paper --force-reselect`
- dry run (no orders): `python -m agent.main --mode paper --dry-run`
- custom profile path: `python -m agent.main --mode paper --profile my_paper_profile.json`
- PowerShell shortcuts: `.\run_paper.ps1` and `.\run_paper_dry.ps1`
- Scheduler helper: `.\register_paper_task.ps1` (and `.\unregister_paper_task.ps1`)
- Scheduler launches `run_paper_scheduled.ps1` once at weekday 8:30, then the script loops every 5 minutes until session end

Trade artifacts:
- `live_trade_summary.json`
- DB table: `live_positions`
- DB table: `live_trades` (closed trades with pnl/r-multiple/exit reason)
- DB table: `live_events`

## Alpaca Environment
Required for `--data-provider alpaca` and live paper execution:

```bash
setx ALPACA_API_KEY "..."
setx ALPACA_SECRET_KEY "..."
setx ALPACA_BASE_URL "https://paper-api.alpaca.markets/v2"
```

After `setx`, open a new terminal (or sign out/in).

Data endpoint is automatically normalized to Alpaca market-data API.

For paper mode with delayed free Alpaca data:
- Use Yahoo for entry/exit signal bars (`live_data_provider: yahoo`)
- Keep Alpaca paper for order execution
- Keep Alpaca for reselection history (`selection_data_provider: alpaca`)

## Notes
- `--dry-run` is strongly recommended before enabling live paper orders.
- For unsupported live exit variants, trade mode falls back to `TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE`.
- Reselection is DB-first and can be forced via `--force-reselect`.

## ORB_R_6 Parity Modes
These modes run the cleanly ported ORB_R_6 engine from `agent/orb_r6` using `orb_r6_config.yaml`.

Prepare candles + opening ranges + reference metrics:

```bash
python -m agent.main --mode r6_run
```

Replay one session (set `asof_date_cst` in `orb_r6_config.yaml` first):

```bash
python -m agent.main --mode r6_replay
```

Live loop (CT session, completed-bar processing):

```bash
python -m agent.main --mode r6_live
```

Paper execution loop (R6 signals + Alpaca paper orders):

```bash
python -m agent.main --mode r6_paper --r6-config r6_stable/config.yaml
```

Custom config path:

```bash
python -m agent.main --mode r6_run --r6-config path/to/config.yaml
```

Research backtest (generates events, trades, variant metrics, yearly returns, subset diagnostics):

```bash
python -m agent.main --mode r6_research --start 2026-01-20 --end 2026-02-23
```

Notes:
- `r6_research` is cache-first: it reuses local `orb_research.db` (`bars_5m`) and only downloads missing sessions.
- Set `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, and either `ALPACA_DATA_URL` or `ALPACA_BASE_URL` only if missing sessions must be backfilled from Alpaca.
- Exit ladder comparisons and stop/exit reason returns are written to:
  - `artifacts/orb_r6/research/r6_variant_metrics.csv`
  - `artifacts/orb_r6/research/r6_exit_reason_performance.csv`

## Isolated R6 Stable Workspace

Use `r6_stable/` if you want this strategy research isolated from other agents.

- Config: `r6_stable/config.yaml`
- DB: `artifacts/r6_stable/orb_core.sqlite`
- Outputs: `artifacts/r6_stable/research/`

Quick commands:

- `.\r6_stable\run_research_full.ps1`
- `.\r6_stable\run_live_signals.ps1`
