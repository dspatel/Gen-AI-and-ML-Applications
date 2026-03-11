# Live tracker

The live tracker is the production-style runner: it continuously pulls intraday bars during a market session, detects multi-horizon reference-range breakouts, and publishes alerts.

## Quick start

From the project root:

```bash
pip install -e .

# Console-only (no Discord)
python -m tools.live_tracker

# Send alerts to Discord (requires a valid webhook URL in config)
python -m tools.live_tracker --send
```

## What it does

Every poll cycle (default: 30s):

1. Determine the current trading session (NYSE calendar + `market.timezone`).
2. Fetch the latest bars from session open up to "now".
3. Compute today’s OR and build reference ranges for configured horizons.
4. Run breakout detection stepwise across the latest bars.
5. Print + optionally send Discord notifications for any new events.

## Flags

- `--config`: path to YAML config (defaults to `config/config.yml`)
- `--symbol`: run a single symbol (defaults to the watchlist)
- `--poll-seconds`: polling cadence (override config)
- `--once`: run a single cycle and exit
- `--send`: enable Discord notifications

You can also set `run.poll_seconds`, `run.status_every`, and `notifications.send_summary` in the config, so you don’t have to pass CLI args.

## Notes and current limitations

This is an intentionally conservative first live implementation:

- It recomputes reference ranges on each cycle (OK for small watchlists). We’ll optimize once behavior is locked.
- State persistence (resume mid-day, de-dupe alerts across restarts) is the next planned increment.
- If started outside market hours, it waits until the next market open (printing a lightweight pre-open status if `run.status_every` > 0).
- When the market closes, it exits and optionally publishes an end-of-day summary (if `notifications.send_summary: true`).
