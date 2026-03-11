# Workspace Layout

This workspace is organized for two active agents (ORB and R6) and a clean path to add a third agent.

## Top-Level

- `agent/`: core Python package (`python -m agent.main ...`)
- `r6_stable/`: isolated R6 configs, scripts, and docs
- `multi_agent_router/`: optional router module
- `docs/`: shared project docs
- `artifacts/`: generated outputs, research files, DBs, diagnostics
- Root config/script files:
  - `paper_profile.json`
  - `orb_r6_config.yaml`
  - `run_paper.ps1`
  - `run_paper_dry.ps1`
  - `run_paper_scheduled.ps1`

## Cleanup Policy

- Keep root focused on:
  - runnable scripts
  - config files
  - core DB (`orb_research.db`)
  - package/docs
- Move ad-hoc analysis exports to:
  - `artifacts/reports/root_exports/<date>/`
- Keep runtime/research outputs under `artifacts/` only.
- `__pycache__` folders should not remain in working folders.

## New Agent Guidance (Agent 3)

Use this structure for the third agent:

- Code:
  - `agent/<new_agent_module>/...`
- Isolated profile/config:
  - `<new_agent_name>_stable/config.yaml`
  - `<new_agent_name>_stable/run_paper.ps1`
  - `<new_agent_name>_stable/docs/...`
- Outputs:
  - `artifacts/<new_agent_name>/...`
- Keep root unchanged except for one short launcher script and one config file if needed.

