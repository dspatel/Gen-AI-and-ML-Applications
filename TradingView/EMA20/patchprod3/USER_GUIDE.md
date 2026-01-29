# EMA20 Scanner User Guide (PRODUCTION)

This folder is the **production** build of the EMA20 Scanner. It is meant to run
on real market days only. There is **no test/replay mode** here.

If you want historical simulation or replay testing, use the separate
**Scanner_TEST** project.

## What the production system does

1. **Build a daily symbol universe** (Step 1)
2. **Fetch/update daily bars into SQLite** (Step 2)
3. **During the session**, run the live tracker to emit LIVE alerts to Discord
4. **After the close**, run the EOD scan (Step 3) and post CSV outputs to Discord

## Normal daily workflow

You can run either the individual steps or let the runner orchestrate it.

### Option A (recommended): One command runner

From the project root:

```bash
python daily_runner.py --mode daily
```

Behavior:
- **Non-trading day**: exits (no side-effects).
- **Before open**: runs Step1 + Step2, then waits for the session window, then starts the live tracker.
- **During session**: ensures Step1 + Step2 have been run for today (creates today’s symbols file & daily-bar cache if missing), then starts the live tracker.
- **After close**: runs Step2 then Step3 (EOD scan) and exits.

Notes:
- The live tracker is configured to auto-stop at the end of the session.
- The runner then runs EOD finalization.

### Option B: Manual steps (debugging)

1) Universe symbols (TradingView export → CSV)
```bash
python run_step1_download_tv.py
```

2) Daily bars → SQLite
```bash
python run_step2_fetch_yf_to_sqlite.py
```

3) Live tracker (intraday monitoring + Discord live alerts)
```bash
python run_live_tracker_yf.py
```

4) EOD scan (daily close breakout scan + Discord CSV uploads)
```bash
python run_step3_scan_from_sqlite.py
```

## Key config knobs (config.py)

### Strategy
- `EMA_PERIOD`: EMA period (default 20)
- `CROSS_LOOKBACK_DAYS`: how far back to look for the anchor “latest cross”
- `WINDOW_DAYS_PRIMARY`: primary breakout window (trading days)
- `WINDOW_DAYS_SECONDARY`: secondary window (trading days)
- `ALLOW_ALERT_ON_CROSS_DATE`: allow breakout on same day as anchor cross
- `REARM_ON_REENTRY` + `REENTRY_MODE`: re-arm logic after a breakout

### Live tracker session
- `TIMEZONE`: must match your intended operating timezone (default America/Chicago)
- `LIVE_SESSION_MODE`: RTH by default
- `LIVE_SESSION_OPEN`, `LIVE_SESSION_CLOSE`: session times (Chicago)
- `LIVE_AUTO_WAIT_FOR_SESSION_START`: if started early, waits until open
- `LIVE_AUTO_STOP_AFTER_SESSION_END`: stops automatically after close

### Discord
- `DISCORD_ENABLED`: master toggle
- `DISCORD_WEBHOOK_URL`: prod webhook
- `DISCORD_SEND_LIVE_ALERTS`: send live alerts
- `DISCORD_SEND_EOD_SUMMARY`: send EOD summary + attach CSVs
- `DISCORD_SEND_EOD_BANNERS`: send “START/DONE” banners

## Where outputs go

- Symbols: `data/symbols/`
- CSV outputs: `data/outputs/`
- SQLite caches: `data/cache/`

## Runtime sanity check

```bash
python tools/print_runtime_config.py
```
