# Runbook

## Environment

Set once (Windows):

- `setx R6_ALPACA_API_KEY "YOUR_KEY"`
- `setx R6_ALPACA_SECRET_KEY "YOUR_SECRET"`
- `setx R6_ALPACA_BASE_URL "https://paper-api.alpaca.markets/v2"` (or generic `ALPACA_BASE_URL`)

Open a new terminal and verify:

- `echo $env:R6_ALPACA_API_KEY`
- `echo $env:R6_ALPACA_SECRET_KEY`

## Daily/Research preflight

1. Confirm config path:
- research: `r6_stable/config.research.yaml`
- paper live: `r6_stable/config.yaml`
2. Confirm isolated DB/output paths:
- `db.path: ./artifacts/r6_stable/orb_core.sqlite`
- `paths.research_output_dir: ./artifacts/r6_stable/research`
3. Confirm Alpaca historical mode:
- in `config.research.yaml`: `market_data.provider: alpaca`
4. Confirm live paper data mode:
- in `config.yaml`: `market_data.provider: yahoo`

## Research execution

- `.\r6_stable\run_research_full.ps1`

## Paper execution

- `.\r6_stable\run_paper.ps1`
- Python runner handles:
  - holiday/session-day checks (NYSE calendar)
  - pre-open wait
  - in-session looping only
  - auto-stop after session end
  - terminal dashboard heartbeat

Switch to Alpaca live data later:

- edit `r6_stable/config.yaml`:
  - `market_data.provider: alpaca`

Discord:

- `r6_stable/config.yaml` -> `discord.enabled` and `discord.webhook_url`
- Keep enabled for event/trade monitoring during paper runs.

## Fold report

- `python -m agent.orb_r6.fold_report --db-path .\artifacts\r6_stable\orb_core.sqlite --run-id <RUN_ID> --locked-variant R6_CONF62_LIMIT1_NO_LONG_PREOR__EMA20_TRAIL_ONLY --output-csv .\artifacts\r6_stable\research\r6_yearly_folds.csv`

## Recovery

- If a run is interrupted, re-run the same command.
- Pipeline is DB-first and idempotent for candles/events.
- Last completed runs are in table `r6_strategy_runs`.
- Paper blocked shorts/missed opportunities are stored in `r6_paper_missed_trades`.
