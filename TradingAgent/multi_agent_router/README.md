# Multi-Agent Router (Isolated Folder)

This router is a single execution layer for one Alpaca account.

- ORB and R6 are treated as **signal sources**.
- Router is the **only broker order writer**.
- This prevents order/position conflicts when both strategies cover the same symbols.

## Decision policy

Per symbol:

1. First signal starts an arbitration window (`decision_window_seconds`).
2. Any additional signals during that window are added.
3. At window end, winner is selected by:
   - `score = source_weight * confidence`
   - tie-breaker: earlier signal timestamp
4. Winner is executed; others are marked as skipped.

If a signal arrives after window closes and a position is already open, it is skipped.
Signals older than `max_signal_age_seconds` are skipped as stale.

Arbitration modes (`execution.arbitration_mode`):

- `first_signal_wins`: execute first signal seen for symbol; later signals are ignored until position closes.
- `score_window`: wait `decision_window_seconds`, then choose highest `source_weight * confidence`.

## What happens if one strategy signals first and another signals later

- If later signal is within arbitration window:
  - both are compared by score, not just first-come.
- If later signal arrives after execution:
  - skipped (`position_open`).

## Runtime behavior

- Session-aware (`NYSE` calendar).
- Runs only `08:30` to `15:00` CT.
- No after-session execution.
- Terminal dashboard heartbeat is always shown.
- Startup/restart reconciliation:
  - If broker has positions not in router state, router imports them as `BROKER_SYNC` open positions.
  - If router state says open but broker is flat, router marks them closed (`BROKER_FLAT_SYNC`).

## Caps and safety

- Per-trade risk cap (`risk_pct_per_trade`).
- Per-trade notional cap % (`max_notional_pct`).
- Absolute per-trade cap (`max_notional_dollars`, default `5000`).
- Short inventory gate (`short_requires_inventory`).

## Files

- `main.py` router engine
- `config.yaml` all settings
- `run_router.ps1` launcher
- `requirements.txt`

State and logs are stored in:

- `artifacts/multi_agent_router/router_state.sqlite`

## Run

From repo root:

```powershell
python .\multi_agent_router\main.py --config .\multi_agent_router\config.yaml
```

or

```powershell
.\multi_agent_router\run_router.ps1
```

## Backtest Arbitration Returns

Run historical replay of router winner-selection:

```powershell
python .\multi_agent_router\backtest_router.py --config .\multi_agent_router\config.yaml --start 2025-01-01 --end 2026-02-24
```

Outputs:

- `artifacts/multi_agent_router/backtests/router_backtest_summary_*.json`
- `artifacts/multi_agent_router/backtests/router_backtest_trades_*.csv`
- `artifacts/multi_agent_router/backtests/router_backtest_skipped_*.csv`

## Important operating model

For clean operation:

- Keep ORB and R6 in **signal-only mode** (dry-run or non-executing modes).
- Let this router be the only process placing broker orders.

## Return testing status

- The arbitration execution engine is implemented and live-safe.
- A dedicated historical return backtest for this exact arbitration policy is not yet added in this folder.
