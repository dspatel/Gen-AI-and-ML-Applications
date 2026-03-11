# Usage

## Standard pipeline (Step 1–3)
Run:
- `python -m src.run`

This performs:
1. yfinance -> SQLite candles
2. ORs -> opening_ranges
3. RR metrics -> daily_reference_metrics

## Replay (Step 4)
Replay simulates a historical day bar-by-bar using stored candles + RR metrics.

Set in `config.yaml`:
- `replay.asof_date_cst`
- `replay.tag_replay_alerts`

Run:
- `python -m src.replay`


## Backtest anchor (as-of date)
Both `src.run` and `src.replay` can anchor computations to a historical session date.

Set in `config.yaml`:
```yaml
run:
  asof_date_cst: "2026-02-10"  # or null for current session
```

Notes:
- `src.run` uses this as the anchor for the ingestion window and computes RR for that as-of date.
- `src.replay` uses `replay.asof_date_cst` and will DB-first backfill candles/OR/RR if needed.


## Canonical as-of date behavior
- `config.yaml asof_date_cst` is the single anchor date for backtests.
- RR at session start uses ONLY prior sessions (`include_today_or=0`).
- After the opening range window completes, RR automatically includes today's OR (`include_today_or=1`).
- Replay switches RR sets at OR end time.


### Shared engine modules
- `src/breakout_engine.py`: shared per-bar breakout evaluation used by Replay (and will be used by Live).
- `src/prepare_asof.py`: DB-first preparation used by Run / Replay / Live.


## Live tracker

Run the live tracker (polls yfinance, stores candles to SQLite, evaluates close-only breakouts and sends Discord alerts if enabled):

- `python -m src.live`

Notes:
- Live always runs for the *current* CST session date; `config.asof_date_cst` is for backtests/replay and is ignored by live.
- Live will refresh post-OR reference rows once the OR window completes.


## Idempotency and restarts

- Live is safe to stop/restart during a session. Breakout events are written idempotently using a natural-key uniqueness constraint.
- On startup, Live restores per-horizon "already broke" state for the current phase (pre-OR vs post-OR) to avoid re-alerting the same horizons.
- Pre-OR and post-OR are treated as distinct phases (`include_today_or` 0 vs 1), so the same horizon can alert once pre-OR and once post-OR if the reference range changes.
