# ORB_HYBRID

Clean project scaffold for hybrid ORB research:
- Phase 1: robust walk-forward mode comparison (`global`, `symbol_specific`, `hybrid`)
- Phase 2: ML trade filter pipeline on exported trade-level dataset

This project currently reuses the proven ORB execution engine from:
- `TradingView/ORB_TEST/backtest_orb_shared_cash.py`

## Setup

```bash
pip install -r TradingView/ORB_HYBRID/requirements.txt
```

## 1) Walk-Forward Mode Comparison

Script: `walk_forward_compare_modes.py`

Default behavior:
- Uses Alpaca feed (`iex`) and independent cash pools.
- Compares these test-fold modes:
  - `global`: one preset for all symbols
  - `symbol_specific`: best preset per symbol
  - `hybrid`: global baseline with per-symbol override only above a train-edge threshold

Run:
```bash
python walk_forward_compare_modes.py --symbols "QQQ,NVDA,SPY" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --cash-per-symbol 10000 --train-days 60 --test-days 20 --step-days 20 --hybrid-min-train-edge 50 --save-fold-csv reports/folds.csv --save-summary-csv reports/summary.csv
```

Useful options:
- `--engine-path` custom path to ORB engine module
- `--cache-db` local SQLite cache path (default `data/market_data_cache.sqlite`)
- `--cache-refresh` force provider refresh
- `--no-cache` disable cache for this run

## 2) Build Trade Dataset for ML Filter

Script: `build_trade_dataset.py`

Purpose:
- Run ORB strategy on historical data.
- Export one row per executed trade with entry-time features and label (`label_win`).

Run:
```bash
python build_trade_dataset.py --symbols "QQQ,NVDA,SPY" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --output-csv data/trade_dataset.csv
```

## 3) Train Baseline ML Trade Filter

Script: `train_trade_filter_baseline.py`

Model:
- Logistic regression (time-split train/test).
- Predicts probability trade is positive (`p_win`).
- Reports classification metrics and PnL impact of filtering trades.

Run:
```bash
python train_trade_filter_baseline.py --dataset-csv data/trade_dataset.csv --train-ratio 0.7 --threshold 0.55 --save-model models/trade_filter_logreg.joblib --save-predictions reports/trade_filter_predictions.csv
```

## 4) Walk-Forward ML Filter (GPU-Ready)

Script: `walk_forward_ml_filter.py`

Purpose:
- Generate ORB trade candidates from multiple presets.
- Train fold-by-fold model to score trade quality (`p_win`).
- Apply thresholded ML filtering on test folds and compare against baseline global preset.
- Report annualized cash growth vs your target.

Run (GPU if available):
```bash
python walk_forward_ml_filter.py --symbols "QQQ,NVDA,SPY" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --train-days 100 --test-days 20 --step-days 20 --target-growth-pct 5 --use-gpu --save-fold-csv reports/ml_filter_folds.csv --save-summary-csv reports/ml_filter_summary.csv
```

Higher-timeframe EMA20 direction gate (close vs EMA20 on completed weekly/monthly candles):
```bash
python walk_forward_ml_filter.py --symbols-file universes/top_liquid_us.txt --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --train-days 100 --test-days 20 --step-days 20 --use-gpu --htf-gate weekly --selection-mode baseline_gate --min-history-days 60
```

Execution cost controls:
- `--slippage-bps` per side (default `5`)
- `--commission-per-share` per side (default `0.005`)

`--htf-gate` options:
- `none` (default)
- `monthly`
- `weekly`
- `both`

`--selection-mode` options:
- `baseline_gate` (default): ML only decides trade/no-trade on the fold's selected baseline preset.
- `candidate_rank`: ML ranks all preset candidates (more aggressive).

`--min-history-days`:
- Enforces warm-up before trades are considered (default `60` trading days).

## 5) Walk-Forward HMM Regime Modes

Script: `walk_forward_hmm_modes.py`

Purpose:
- Keep ORB baseline preset selection per fold.
- Fit HMM regime states on train windows.
- Compare out-of-sample modes:
  - `baseline`
  - `hmm_global` (single global symbol regime)
  - `hmm_symbol` (per-symbol regimes)
  - `hmm_hybrid` (per-symbol fallback to global)

Run:
```bash
python walk_forward_hmm_modes.py --symbols-file universes/top_liquid_us.txt --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --train-days 100 --test-days 20 --step-days 20 --min-symbols 10 --min-history-days 60 --cash-per-symbol 10000 --start-shares-each 100 --trade-fraction 0.2 --slippage-bps 5 --commission-per-share 0.005 --target-growth-pct 5 --htf-gate weekly --hmm-global-symbol SPY --hmm-states 3 --hmm-min-train-samples 80 --hmm-min-state-trades 10 --save-fold-csv reports/hmm_modes_folds.csv --save-summary-csv reports/hmm_modes_summary.csv
```

## Credentials (PowerShell)

```powershell
$env:APCA_API_KEY_ID="your_key"
$env:APCA_API_SECRET_KEY="your_secret"
```

## Next Phase

- Move shared ORB execution code from `ORB_TEST` into `ORB_HYBRID` internal modules.
- Add walk-forward ML gating test:
  - train model on train window
  - score test trades
  - compare filtered PnL vs unfiltered hybrid baseline.
