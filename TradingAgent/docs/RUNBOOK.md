# Runbook

## 1) One-time setup

1. Set Alpaca paper credentials (persistent for scheduled task):
   - `setx ALPACA_API_KEY "YOUR_KEY"`
   - `setx ALPACA_SECRET_KEY "YOUR_SECRET"`
   - `setx ALPACA_BASE_URL "https://paper-api.alpaca.markets/v2"`
   - Restart terminal (or sign out/in) after `setx`
2. Verify credentials in a new shell:
   - `echo $env:ALPACA_API_KEY`
   - `echo $env:ALPACA_BASE_URL`
3. Optional connectivity test:
   - `python -m agent.main --mode paper_live --dry-run`
4. Install dependencies:
   - `pip install -r requirements.txt`
5. Verify dry-run:
   - `python -m agent.main --mode paper_live --dry-run`

Environment variable names expected by the agent:
   - ORB runner (preferred): `ORB_ALPACA_API_KEY`, `ORB_ALPACA_SECRET_KEY`, optional `ORB_ALPACA_BASE_URL`
   - Generic fallback: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2`

## 2) Daily run (paper trading)

Run once at any time. Python loop handles pre-open wait, session-only execution, and auto-stop after session:

- `python -m agent.main --mode paper_live`

Or register a Windows scheduled task once:

- `.\register_paper_task.ps1`

Scheduler behavior after update:

- One scheduler trigger at weekday `08:30`
- `run_paper_scheduled.ps1` starts Python `paper_live`; Python loop runs until `15:00 CT`
- If startup is late and you miss the trigger, run manually: `.\run_paper.ps1`

The agent will:

1. Reselect monthly/quarterly if due
2. Load active strategy map from DB
3. Pull live bars (Yahoo by default in paper profile)
4. Evaluate ORB entries
5. Place/manage Alpaca paper orders
6. Persist events and trade lifecycle rows
7. Render a terminal dashboard heartbeat
8. Record blocked/missed trades (for example SHORT blocked due no inventory)

Discord notifications (ORB paper agent):

- Config in `paper_profile.json`:
  - `discord_enabled`
  - `discord_webhook_url`
- Sends:
  - entry opened
  - entry signal detected
  - position closed
  - warnings/errors

## 3) Daily verification

Check summary file:

- `live_trade_summary.json`

Check DB tables:

- `strategy_selections`
- `live_positions`
- `live_trades`
- `live_events`
- `missed_trades`

Quick SQL examples (SQLite):

1. `select count(*) from live_positions where status='OPEN';`
2. `select symbol, exit_reason, pnl, r_mult from live_trades order by exit_ts desc limit 20;`
3. `select symbol, strategy_id, asof_date from strategy_selections where is_active=1;`

## 4) Common failure actions

1. No data for symbols:
   - Verify internet connection
   - Verify Yahoo access
2. No orders placed in non-dry mode:
   - Verify Alpaca keys
   - Verify `ALPACA_BASE_URL` points to paper endpoint
3. Strategy map missing:
   - Run forced reselection:
   - `python -m agent.main --mode paper_live --force-reselect`

## 5) Safety defaults

- Start with `--dry-run`
- Use small `risk_pct_per_trade` (default `0.005`)
- Keep `max_open_positions` limited (default `8`)

## 6) Stop scheduler

- `.\unregister_paper_task.ps1`
