# Runbook

This doc is the "do this, then that" guide for **TEST replay** and **live runs**.

## 0) Install
From repo root:

```bash
pip install -r requirements.txt
pip install -e .
pytest -q
```

## 1) Configure
Edit:

- `config/config.example.yml`

Key sections:

- `data.interval`: `15m` (recommended for this project)
- `reference.horizons`: list like `[3,5,9]`
- `storage.mode`: `TEST` or `PROD`
- `notifications.enabled`: set true when ready
- `notifications.discord.webhook_url`: your Discord webhook

## 2) Build daily metrics (Step 5)
Computes:
- Daily OR for lookback sessions
- Reference ranges for each configured horizon
- Behavior + overlap stats
- Writes a day-centric CSV for all symbols/horizons
- Upserts to SQLite (v2 tables) if storage is enabled

```bash
python -m tools.demo_step5
```

Output:
- `reports/daily/<DATE>_metrics.csv`

## 3) Detect breakouts (Step 6)
Runs a **ladder breakout detector** over the session bars for the asof day:
- evaluates each horizon reference range
- records the first break and later breaks
- records "already broke" horizons
- writes per-symbol events CSV
- upserts to SQLite (v2 tables)

```bash
python -m tools.demo_step6
```

Output:
- `reports/daily/<DATE>_<SYMBOL>_events.csv`

## 4) Replay a day (step-by-step alerts)
This is the tool to see how alerts would have arrived **as the day progressed**.
It walks bar-by-bar, and can optionally send Discord alerts.

```bash
python -m tools.replay_day --date 2026-02-05 --symbol SPY --print-events
```

Send to Discord too:

```bash
python -m tools.replay_day --date 2026-02-05 --symbol SPY --send
```

Live-like delay (e.g., 0.25s per bar):

```bash
python -m tools.replay_day --date 2026-02-05 --symbol SPY --send --delay 0.25
```

## 5) Database files
Stored under `db/` (gitignored):

- `db/orb_ref_test.sqlite`
- `db/orb_ref_prod.sqlite`

Key tables used by current steps:

- `daily_opening_ranges`
- `daily_symbol_metrics_v2`
- `breakout_events_v2`



### Note on dates and outputs
- `tools.demo_step6` uses `run.asof_date` from `config/config.example.yml` and writes `reports/daily/{asof_date}_*_events.csv`.
- `tools.replay_day --date YYYY-MM-DD` writes `reports/replay/{date}_{symbol}_replay_events.csv`.

### Discord notifications
- Set `notifications.enabled: true`.
- In TEST mode, also set `notifications.send_in_test_mode: true`.
- Replay requires `--send` to actually post to Discord (so you don't spam accidentally).


## Notifications format

See `docs/NOTIFICATIONS.md` for the exact alert fields and where to customize wording/icons.
