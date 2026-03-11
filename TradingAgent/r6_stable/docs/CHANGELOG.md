# Changelog

## 2026-02-24

- Added isolated R6 stable workspace:
  - `r6_stable/config.yaml`
  - `r6_stable/config.research.yaml`
  - `r6_stable/symbols.csv`
  - `r6_stable/run_research_full.ps1`
  - `r6_stable/run_live_signals.ps1`
  - `r6_stable/run_paper.ps1`
- Added dedicated docs:
  - `r6_stable/docs/USAGE.md`
  - `r6_stable/docs/RUNBOOK.md`
  - `r6_stable/docs/SCHEMA.md`
  - `r6_stable/docs/STRATEGY_SPEC.md`
- Added configurable output/cache paths for R6:
  - `paths.research_output_dir`
  - `paths.cache_source_db_path`
- Added fold validation utility:
  - `python -m agent.orb_r6.fold_report`
- Added paper execution module:
  - `python -m agent.main --mode r6_paper --r6-config r6_stable/config.yaml`
  - Yahoo live bars now, switchable to Alpaca later via config.
