# OPTIONS_AGENT

Ground-up options backtest scaffold using Alpaca options data.

## What this is

- A new options-first agent, separate from ORB equity projects.
- Uses Alpaca:
  - Underlying daily bars (`/v2/stocks/bars`)
  - Option contracts (`/v2/options/contracts`)
  - Option daily bars (`/v1beta1/options/bars`)
- Simulates cash-only options trading with position sizing.

## Strategy implemented (v1)

- Signal is built from underlying daily bars:
  - `long` signal when close is above EMA20 and above prior N-day high.
  - `short` signal when close is below EMA20 and below prior N-day low.
- On signal day:
  - Buy a call for `long`, buy a put for `short`.
  - Contract is chosen near target moneyness with DTE window.
- Exit rules:
  - take-profit %
  - stop-loss %
  - max hold days
  - expiry/last available bar fallback
- Capital:
  - shared cash pool
  - each trade uses fixed fraction of available cash
  - max open positions cap

## Run

```powershell
$env:APCA_API_KEY_ID="your_key"
$env:APCA_API_SECRET_KEY="your_secret"
conda run -n jobapp-agent python TradingView/OPTIONS_AGENT/backtest_options_agent.py --symbols "SPY,QQQ,NVDA" --period-days 420 --start-cash 100000 --trade-fraction 0.10
```

Useful options:

- `--side-mode both|long|short`
- `--min-dte 20 --max-dte 45`
- `--hold-days 5`
- `--stop-loss-pct 0.35 --take-profit-pct 0.60`
- `--moneyness-pct 0.01`
- `--no-cache` (skip sqlite cache for quick tests)

## Notes

- This is a realistic starting framework, not a final production model.
- It currently uses daily option bars (not intraday bid/ask execution simulation).
- Next step is adding spread/slippage/fill modeling and a purged walk-forward evaluator.
