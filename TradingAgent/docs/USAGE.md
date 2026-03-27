# Usage

## Quick start (ORB paper live mode)

Run the default paper profile:

- `python -m agent.main --mode paper_live`

Dry-run (no broker orders):

- `python -m agent.main --mode paper_live --dry-run`

Single-cycle execution (one pass only):

- `python -m agent.main --mode paper`

PowerShell shortcuts:

- `.\run_paper.ps1`
- `.\run_paper_dry.ps1`

## Paper profile

Defaults are stored in `paper_profile.json`.

Key fields:

- `symbols`
- `live_data_provider` (current paper profile: `alpaca`)
- `live_alpaca_feed` (`sip` for paid Alpaca real-time stock data)
- `selection_data_provider` (default: `alpaca`)
- `frequency` (`monthly` or `quarterly`)
- `risk_pct_per_trade`
- `max_open_positions`
- `max_notional_dollars` (hard dollar cap per trade)
- `discord_enabled`
- `discord_webhook_url`
- `live_poll_seconds`
- `live_session_calendar` (holiday/session handling)
- `live_wait_for_open`
- `live_dashboard`
- `short_requires_inventory` (if `true`, SHORT only when long shares exist for symbol)
- `gap_entry_enabled` (enable 15m opening-gap add-on entry logic)
- `gap_entry_timeframe_min` (default `15`)
- `gap_entry_apply_on_limit1` (default `false`; avoid consuming the only daily slot on LIMIT1 variants)
- `gap_entry_gap_threshold`, `gap_entry_ema_dist_min`, `gap_entry_ema_dist_max`

## Reselect only

Run strategy reselection without placing orders:

- `python -m agent.main --mode reselect --symbols SPY,AAPL,NVDA --asof 2026-02-23 --frequency monthly --side-mode long_only --lookback-months 18 --validation-months 6 --min-train-trades 30 --min-val-trades 10 --data-provider alpaca`

## Research mode

Run full strategy matrix backtest for one symbol/date window:

- `python -m agent.main --mode orb --symbol SPY --start 2025-01-01 --end 2025-12-31 --data-provider alpaca`

## Daily production command

The intended daily paper command is:

- `python -m agent.main --mode paper_live`

## ORB_R6 research reuse (cache-first)

Run ORB_R6 research:

- `python -m agent.main --mode r6_research --start 2025-01-01 --end 2025-12-31 --symbols SPY,QQQ`

Behavior:

- Reuses local `orb_research.db` (`bars_5m`) first.
- Builds `15m` candles from cached `5m` when needed.
- Only backfills missing sessions using `market_data.provider` from `orb_r6_config.yaml`.

## Isolated R6 workspace

If you want this module fully separated from other outputs, use:

- `r6_stable/config.yaml`
- `.\r6_stable\run_research_full.ps1`
- `.\r6_stable\run_paper.ps1`
