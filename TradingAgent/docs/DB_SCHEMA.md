# DB Schema

Main SQLite file: `orb_research.db`

## Core research tables

- `bars_5m`
- `strategy_runs`
- `trades`
- `metrics`

## Production selection tables

- `strategy_selections`
  - one row per symbol per reselection cycle
  - stores train/validation metrics and rank score
  - `is_active=1` indicates current map

## Production execution tables

- `live_positions`
  - open and closed live/paper positions
  - stop state and lifecycle state machine fields
- `live_trades`
  - finalized closed trade record
  - includes `entry_price`, `exit_price`, `pnl`, `pnl_pct`, `r_mult`, `exit_reason`
- `live_events`
  - structured operational events and errors

## Typical audit queries

1. Latest active strategy map:
   - `select symbol, strategy_id, asof_date from strategy_selections where is_active=1 order by symbol;`
2. Open positions:
   - `select symbol, side, qty, entry_price, stop_price from live_positions where status='OPEN';`
3. Last closed trades:
   - `select symbol, exit_reason, pnl, r_mult from live_trades order by exit_ts desc limit 50;`

