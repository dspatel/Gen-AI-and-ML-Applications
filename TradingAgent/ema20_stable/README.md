# EMA20 Stable Agent

This workspace isolates EMA20 multi-timeframe research from ORB/R6 modules.

Data source modes (in `market_data.provider`):

- `r6_cache`: reuse only local R6 candles.
- `alpaca`: fetch from Alpaca historical into EMA20 DB.
- `auto`: copy from R6 first, then backfill missing outer ranges from Alpaca.

## Quick start

- Full research run:
  - `.\ema20_stable\run_research_full.ps1`

Equivalent:

- `python -m agent.main --mode ema20_research --start 2023-01-03 --end 2026-02-23 --ema20-config .\ema20_stable\config.research.yaml`

Outputs:

- `artifacts/ema20_stable/research/ema20_summary_<timestamp>.json`
- `artifacts/ema20_stable/research/ema20_variant_metrics_<timestamp>.csv`
- `artifacts/ema20_stable/research/ema20_trades_<timestamp>.csv`

## Walk-forward (train/validate/test + ablation)

- `.\ema20_stable\run_walkforward.ps1`

Equivalent:

- `python -m agent.main --mode ema20_walkforward --end 2026-02-23 --symbols SPY --ema20-config .\ema20_stable\config.walkforward.optimized.yaml`

Configs:

- `config.walkforward.yaml`: broad exploration grid.
- `config.walkforward.optimized.yaml`: compact production-candidate grid (faster, validation-focused).

## Rolling Walk-Forward (robustness)

- `.\ema20_stable\run_rolling_walkforward.ps1`

Equivalent:

- `python -m agent.main --mode ema20_rolling --start 2023-01-03 --end 2026-02-23 --symbols SPY --ema20-config .\ema20_stable\config.walkforward.optimized.yaml --ema20-train-months 18 --ema20-validate-months 6 --ema20-test-months 3 --ema20-step-months 6`
  - Optional gate: `--ema20-min-test-excess-vs-bh 0.0` (require test split to beat equal-weight buy-and-hold).

Artifacts:

- `ema20_rolling_folds_<timestamp>.csv`
- `ema20_rolling_splits_<timestamp>.csv`
- `ema20_rolling_summary_<timestamp>.json`

## Buy-and-Hold Comparison

Each `ema20_research` run now exports:

- `ema20_buyhold_symbols_<timestamp>.csv` (per-symbol buy-and-hold return for run window)
- `ema20_variant_vs_buyhold_<timestamp>.csv` (per-variant, per-symbol excess vs buy-and-hold)

`ema20_variant_metrics_<timestamp>.csv` includes:

- `buyhold_equal_weight_return_pct`
- `excess_vs_buyhold_equal_weight_pct`
- `avg_symbol_excess_vs_buyhold_pct`
- `symbols_beating_buyhold_count`

Artifacts:

- `ema20_walkforward_<timestamp>.csv`
- `ema20_ablation_entry_<timestamp>.csv`
- `ema20_ablation_exit_<timestamp>.csv`
- `ema20_ablation_lookback_<timestamp>.csv`
- `ema20_ablation_flat_<timestamp>.csv`
- `ema20_ablation_chop_<timestamp>.csv`
- `ema20_ablation_max_open_<timestamp>.csv`
- `ema20_ablation_max_new_<timestamp>.csv`
- `ema20_walkforward_summary_<timestamp>.json`

## Alpaca Credentials

Required when using `market_data.provider: alpaca` or `auto`:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- Optional: `ALPACA_DATA_URL` (defaults to `https://data.alpaca.markets/v2`)
