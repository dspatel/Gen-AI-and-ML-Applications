
# ORB Reference Range Project

Implemented through **Step 5** (combined).

## Steps included
- Step 2: Config-driven intuition labels (+ optional icons)
- Step 3: Trading-session helper (market days only; exchange_calendars version compatible)
- Step 4: Deterministic per-session intraday fetch with CSV cache + offline-testable provider interface
- Step 5: Daily OR + Reference Range + Lookback Behavior metrics + **day-centric reporting across all symbols**

## Quick start
```bash
pip install -r requirements.txt
pytest -q
python -m tools.demo_labels
python -m tools.demo_fetch
python -m tools.demo_step5
python -m tools.demo_step6
```

## Outputs
- `reports/daily/<YYYY-MM-DD>_metrics.csv` (one file per day, all symbols, `symbol` column)
- Cache is internal: `cache/<SYMBOL>/<INTERVAL>/<YYYY-MM-DD>.csv` (gitignored)

## Install for running tools (recommended)
```bash
pip install -r requirements.txt
pip install -e .
```

Now you can run:
```bash
python -m tools.demo_step5
python -m tools.demo_step6
```

## Step 7: SQLite storage

This repo can optionally upsert daily metrics and breakout events into SQLite.

Configure in `config/config.example.yml` under `storage:`.

### Run + write to DB
```bash
python -m tools.demo_step5
python -m tools.demo_step6
```

DB files (gitignored):
- `db/orb_ref_test.sqlite` (storage.mode=TEST)
- `db/orb_ref_prod.sqlite` (storage.mode=PROD)

### Backfill a date range (trading days)
```bash
python -m tools.backfill --start 2026-02-01 --end 2026-02-10 --mode TEST
```

### Repair a date range (delete then rerun)
```bash
python -m tools.repair_range --start 2026-02-01 --end 2026-02-05 --mode TEST
```

## Documentation
- Metrics & intuition: `docs/METRICS_GUIDE.md`

## Step 8: Decision layer
- Rules: `config/decision_rules.yml`
- Events now include `decision`, `confidence`, and `decision_reasons`.
