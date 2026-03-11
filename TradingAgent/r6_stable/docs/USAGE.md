# Usage

## 1) Full historical research (isolated workspace, Alpaca historical)

- `.\r6_stable\run_research_full.ps1`

Equivalent:

- `python -m agent.main --mode r6_research --start 2023-01-03 --end 2026-02-23 --r6-config .\r6_stable\config.research.yaml`

Outputs:

- `artifacts/r6_stable/research/r6_summary.json`
- `artifacts/r6_stable/research/r6_variant_metrics.csv`
- `artifacts/r6_stable/research/r6_yearly_returns.csv`
- `artifacts/r6_stable/research/r6_trades.csv`
- `artifacts/r6_stable/research/r6_subset_performance.csv`
- `artifacts/r6_stable/research/r6_exit_reason_performance.csv`
- `artifacts/r6_stable/research/r6_confidence_sizing_report.csv`

## 2) Yearly fold validation report

- `python -m agent.orb_r6.fold_report --db-path .\artifacts\r6_stable\orb_core.sqlite --run-id <RUN_ID> --locked-variant R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY --output-csv .\artifacts\r6_stable\research\r6_yearly_folds.csv`

## 3) Paper execution loop (Yahoo live bars + Alpaca paper orders)

- `.\r6_stable\run_paper.ps1`

Equivalent:

- `python -m agent.main --mode r6_paper --r6-config .\r6_stable\config.yaml`

Discord for R6 paper:

- Configure in `r6_stable/config.yaml`:
  - `discord.enabled: true`
  - `discord.webhook_url: <webhook>`
- Sends:
  - breakout event alerts with full R6 metrics
  - entry opened (includes confidence, horizon, RR bounds/width, include_today_or, risk)
  - position closed (includes pnl, r_mult, exit reason)
  - warnings/errors

Terminal dashboard:

- `r6_stable/config.yaml` -> `paper.dashboard: true`
- Shows heartbeat, cycles, bars processed, entries, closes, and last action.

Sizing and short inventory controls (R6 paper):

- `paper.max_notional_dollars` hard dollar cap per trade (default `5000`)
- `paper.short_requires_inventory: true` blocks SHORT unless long shares exist
- `paper.confidence_sizing_enabled` to scale size by confidence

## 4) Live signal tracker (no broker orders)

- `.\r6_stable\run_live_signals.ps1`

Equivalent:

- `python -m agent.main --mode r6_live --r6-config .\r6_stable\config.yaml`

Note:

- `r6_live` is signal/event tracking only. It does not place paper/live broker orders.
