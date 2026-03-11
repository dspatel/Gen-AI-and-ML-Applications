# Changelog

## 1.0.0

- Added production paper-trading workflow with simplified `--mode paper`
- Added profile-based configuration file `paper_profile.json`
- Added Yahoo 5m signal-data provider support
- Kept Alpaca paper broker execution support
- Added periodic reselection engine with prior-data-only discipline
- Added DB tables for selection, live positions, live events, and live trades
- Added broker reconciliation path (`BROKER_SYNC_FLAT`)
- Added PowerShell helpers:
  - `run_paper.ps1`
  - `run_paper_dry.ps1`
- Added comprehensive docs in `docs/`

