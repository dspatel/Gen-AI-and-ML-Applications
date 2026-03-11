# ORB_TEST (QQQ/NVDA/SPY, 15m, 60d)

This project simulates opening-range-breakout strategies on intraday data from either:
- `yfinance` (default)
- `tvdatafeed` (optional, can provide longer intraday history)
- `alpaca` (free API key, no TradingView account required)

It also supports local bar caching in SQLite to reduce repeated provider calls.

## 1) Single-symbol book (independent cash)

Script: `backtest_orb_strategy.py`

Rules (aligned with `monitor_proj_v2` breakout confirmation):
- Opening Range (OR): first 30 minutes of regular session (09:30-10:00 ET)
- Confirmed breakout:
  - UP: breakout bar close > OR high, and next bar close > breakout close
  - DOWN: breakout bar close < OR low, and next bar close < breakout close
- Only the first confirmed breakout in the first half of the session is traded.
- Entry price: confirmation-bar close.
- Exit price: same-day regular-session close.

Portfolio defaults:
- Initial: 100 shares + $10,000 cash
- UP signal: buy using 20% of available cash, then sell same qty at close
- DOWN signal: sell 20% of available shares, then buy same qty back at close

Run:
```bash
python backtest_orb_strategy.py
```

## 2) Multi-symbol shared-cash book (configurable)

Script: `backtest_orb_shared_cash.py`

Defaults:
- Symbols: `QQQ,NVDA,SPY`
- Start with 100 shares of each symbol
- Single shared cash pool: `$10,000`
- Same ORB entry/exit and 20% sizing rules

Run baseline:
```bash
python backtest_orb_shared_cash.py --symbols "QQQ,NVDA,SPY"
```

Use a larger universe from file:
```bash
python backtest_orb_shared_cash.py --symbols-file "universes/top_liquid_us.txt" --data-source tvdatafeed --tv-n-bars 12000 --period 420d --interval 15m
```

Universe file format:
- `SYMBOL` (uses `--tv-default-exchange`)
- or `SYMBOL:EXCHANGE` (recommended for tvdatafeed)

### Data source options

Default is Yahoo:
```bash
python backtest_orb_shared_cash.py --data-source yfinance --period 60d
```

TradingView via tvdatafeed:
```bash
python backtest_orb_shared_cash.py --data-source tvdatafeed --tv-n-bars 5000 --tv-exchanges "QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX" --period 120d
```

Alpaca free feed (`iex`) with a longer lookback:
```bash
python backtest_orb_shared_cash.py --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --symbols "QQQ,NVDA,SPY"
```

Set Alpaca credentials (or pass `--alpaca-key` / `--alpaca-secret`):
```powershell
$env:APCA_API_KEY_ID="your_key"
$env:APCA_API_SECRET_KEY="your_secret"
```

### Local cache (SQLite)

By default, bars are cached in:
- `TradingView/ORB_TEST/data/market_data_cache.sqlite`

Cache controls:
- `--cache-db <path>` use a custom SQLite file
- `--cache-refresh` force refetch from provider and overwrite cache rows
- `--no-cache` disable cache reads and writes

Example:
```bash
python backtest_orb_shared_cash.py --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --cache-refresh
```

Install `tvdatafeed` (not on PyPI, install from GitHub):
```bash
pip install --upgrade --no-cache-dir git+https://github.com/rongardF/tvdatafeed.git
```

Optional TradingView login for better access:
```bash
set TVDATAFEED_USERNAME=your_username
set TVDATAFEED_PASSWORD=your_password
```

### Optional strategy filters

- `--direction-mode both|up|down`
- `--min-breakout-frac 0.15` (minimum breakout distance vs OR width)
- `--min-rvol 1.3` with `--rvol-lookback-bars 6`
- `--use-vwap-filter`
- `--use-ema-slope-filter`
- `--entry-cutoff 11:00` (ET, still capped at first-half boundary)

### Exit controls

- `--exit-mode close|bracket`
- `--stop-or-mult 0.6`
- `--target-or-mult 1.2`
- `--time-stop-bars 3`
- `--min-progress-r 0.3`
- `--break-even-r 0.7`
- `--trail-mode none|ema|vwap`
- `--trail-after-r 1.0`

Example filtered run:
```bash
python backtest_orb_shared_cash.py --symbols "QQQ,NVDA,SPY" --direction-mode up --min-breakout-frac 0.15 --min-rvol 1.3 --use-vwap-filter --use-ema-slope-filter --entry-cutoff 11:00 --exit-mode bracket --stop-or-mult 0.6 --target-or-mult 1.2
```

Example advanced exit stack:
```bash
python backtest_orb_shared_cash.py --symbols "QQQ,NVDA,SPY" --data-source tvdatafeed --tv-n-bars 12000 --period 420d --exit-mode bracket --stop-or-mult 0.6 --target-or-mult 1.2 --time-stop-bars 3 --min-progress-r 0.3 --break-even-r 0.7 --trail-mode ema --trail-after-r 1.0
```

### Experiment sweep table

Run built-in preset variants and compare cash impact:

```bash
python backtest_orb_shared_cash.py --symbols "QQQ,NVDA,SPY" --data-source tvdatafeed --period 120d --tv-n-bars 5000 --run-experiments
```

The table reports `cash_change`, `strategy_pnl`, and `alpha_vs_bh` so you can identify variants that improve cash instead of relying on stock appreciation alone.

## 3) Meta Strategy Selection (Blend Presets)

Script: `backtest_orb_meta.py`

Purpose:
- Train on an earlier slice of dates.
- Choose the best preset per symbol from a preset pool.
- Test out-of-sample on the remaining dates.
- Compare blended-per-symbol selection vs baseline preset.

Example:
```bash
python backtest_orb_meta.py --symbols "QQQ,NVDA,SPY" --data-source tvdatafeed --period 420d --interval 15m --tv-n-bars 12000 --tv-exchanges "QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX" --train-ratio 0.6 --start-shares 100 --start-cash-per-symbol 10000 --trade-fraction 0.2
```

With universe file:
```bash
python backtest_orb_meta.py --symbols-file "universes/top_liquid_us.txt" --data-source tvdatafeed --period 420d --interval 15m --tv-n-bars 12000 --train-ratio 0.6
```

Alpaca free feed example:
```bash
python backtest_orb_meta.py --symbols-file "universes/top_liquid_us.txt" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --train-ratio 0.6
```

## 4) Exit Grid Search

Script: `exit_grid_search.py`

Shared pool example (single `$30,000`):
```bash
python exit_grid_search.py --symbols "QQQ,NVDA,SPY" --data-source tvdatafeed --period 420d --interval 15m --tv-n-bars 12000 --tv-exchanges "QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX" --pool-mode shared --shared-cash-total 30000 --top-n 10
```

Independent pools example (`$10,000` per symbol):
```bash
python exit_grid_search.py --symbols "QQQ,NVDA,SPY" --data-source tvdatafeed --period 420d --interval 15m --tv-n-bars 12000 --tv-exchanges "QQQ:NASDAQ,NVDA:NASDAQ,SPY:AMEX" --pool-mode independent --cash-per-symbol 10000 --top-n 10
```

With universe file:
```bash
python exit_grid_search.py --symbols-file "universes/top_liquid_us.txt" --data-source tvdatafeed --period 420d --interval 15m --tv-n-bars 12000 --pool-mode shared --shared-cash-total 100000 --top-n 10
```

Alpaca free feed example:
```bash
python exit_grid_search.py --symbols-file "universes/top_liquid_us.txt" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --pool-mode independent --cash-per-symbol 10000 --top-n 10
```

## 5) Walk-Forward Strategy Validation

Script: `walk_forward_runner.py`

This performs rolling train/test evaluation:
- pick best preset on each train window
- evaluate on the next unseen test window
- compare selected preset vs baseline (`close` exit)

Example:
```bash
python walk_forward_runner.py --symbols-file "universes/top_liquid_us.txt" --data-source tvdatafeed --period 420d --interval 15m --tv-n-bars 12000 --pool-mode independent --cash-per-symbol 10000 --train-days 60 --test-days 20 --step-days 20 --min-symbols 10
```

Alpaca free feed example:
```bash
python walk_forward_runner.py --symbols-file "universes/top_liquid_us.txt" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --pool-mode independent --cash-per-symbol 10000 --train-days 60 --test-days 20 --step-days 20 --min-symbols 10
```

## 6) Walk-Forward Mode Comparison (Global vs Symbol-Specific vs Hybrid)

Script: `walk_forward_compare_modes.py`

Purpose:
- `global`: one preset selected on train and applied to all symbols
- `symbol_specific`: best train preset chosen independently per symbol
- `hybrid`: starts from global, then allows symbol override only if train edge exceeds threshold

Example (Alpaca, independent pools):
```bash
python walk_forward_compare_modes.py --symbols "QQQ,NVDA,SPY" --data-source alpaca --alpaca-feed iex --period 420d --interval 15m --cash-per-symbol 10000 --train-days 60 --test-days 20 --step-days 20 --hybrid-min-train-edge 50
```

Output includes:
- fold-level test cash changes for all three modes
- total and median fold cash change
- positive-fold rate
- max drawdown of cumulative fold cash
