# R6 Stable Agent

This workspace isolates the regime-aware ORB_R6 research module from other agents.

## Clean destination

- Live paper config: `r6_stable/config.yaml` (Yahoo live bars)
- Research config: `r6_stable/config.research.yaml` (Alpaca historical)
- DB: `artifacts/r6_stable/orb_core.sqlite`
- Research outputs: `artifacts/r6_stable/research/`
- Symbol universe: `r6_stable/symbols.csv`

## Quick start

- Full research:
  - `.\r6_stable\run_research_full.ps1`
- Paper execution loop:
  - `.\r6_stable\run_paper.ps1`
- Live signal tracking (no broker orders):
  - `.\r6_stable\run_live_signals.ps1`

## Documentation

- `r6_stable/docs/USAGE.md`
- `r6_stable/docs/RUNBOOK.md`
- `r6_stable/docs/SCHEMA.md`
- `r6_stable/docs/STRATEGY_SPEC.md`
- `r6_stable/docs/CHANGELOG.md`
