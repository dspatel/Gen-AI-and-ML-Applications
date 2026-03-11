# ORB Reference Range Project (Horizon Ladder)

This repo builds **Opening Range (OR)** based **reference ranges** and detects intraday **breakouts**.
New capability: **multiple horizons** (e.g., `[3,5,9]`) + **ladder alerts**.

## What’s implemented
- Session calendar utilities (trading days, OR window bounds)
- Deterministic intraday fetch with CSV cache + offline-testable provider interface
- Daily OR computation + DB persistence (`daily_opening_ranges`)
- Multi-horizon reference ranges (`reference.horizons`)
- Lookback behavior metrics + OR overlap stats
- Intuition labels (config-driven) + market story text (icons optional)
- Breakout detection with:
  - close-only confirmation candles (`confirm_closes`)
  - re-arm logic (`inside_reset_pct`)
  - ladder tracking across horizons
- SQLite storage:
  - `daily_symbol_metrics_v2`
  - `breakout_events_v2`
- Replay tool that walks a day bar-by-bar (optionally sends Discord alerts)
- Live tracker that polls during a session (optionally sends Discord alerts)

## Quick start
```bash
pip install -r requirements.txt
pip install -e .
pytest -q
python -m tools.demo_step5
python -m tools.demo_step6
python -m tools.replay_day --date 2026-02-05 --symbol SPY --print-events
python -m tools.live_tracker
```

## Configuration
Edit `config/config.yml`.

Tip: `config/config.example.yml` is a commented template you can copy/compare against.

Important knobs:
- `reference.horizons`: list like `[3,5,9]`
- `data.interval`: e.g. `15m`
- `breakouts.confirm_closes`: confirmation bars required
- `notifications.enabled`: enable Discord sends

## Docs
- Runbook (how to run/replay): `docs/RUNBOOK.md`
- Horizons + ladder logic: `docs/HORIZONS_AND_LADDER.md`
- Metrics & intuition: `docs/METRICS_GUIDE.md`
- Live runner: `docs/LIVE_TRACKER.md`

