# Notification Format

The agent writes structured operational events to:

- DB table: `live_events`
- File summary: `live_trade_summary.json`

## Event payload schema

`live_events` columns:

- `event_id`
- `created_at` (UTC ISO)
- `level` (`INFO`, `WARN`, `ERROR`)
- `symbol` (nullable)
- `event_type`
- `message`
- `data_json` (structured detail)

## Recommended alert mapping

- `entry_signal_detected` -> confirmed OR breakout signal (pre-order decision snapshot)
- `entry_opened` -> trade opened
- `position_closed` -> trade closed with reason
- `strategy_fallback` -> unsupported strategy variant replaced
- `error` -> execution failure

## Rich Discord format (ORB + R6)

Both agents now send multi-section Discord alerts with emoji indicators:

- Header: level + event type
- Context: time, symbol, side
- Strategy: `strategy_id`, timeframe, exit plan
- Trade: qty, entry, stop, risk
- Structure: OR/ref range details (when available)
- Protection: stop attach status and broker order ids
- Exit: reason, exit price, PnL, R-multiple

### Exit-plan fields now included

- `exit_variant` (ORB)
- `trade_limit_1d` (ORB)
- `long_cutoff_ct` (ORB)
- `exit_strategy` (R6)
- `time_exit_ct` (R6)

## Example `data_json` (entry)

```json
{
  "strategy_id": "TF15_STACK_TSNP_UNLIMITED_LONG_CUTOFF_NONE",
  "side": "LONG",
  "entry_ts": "2026-02-24 10:15:00+0000",
  "qty": 12,
  "entry_price": 523.41,
  "stop_price": 520.08,
  "risk": 3.33,
  "or_high": 522.88,
  "or_low": 520.22,
  "or_width": 2.66,
  "entry_bar_open": 523.05,
  "entry_bar_high": 523.62,
  "entry_bar_low": 522.91,
  "entry_bar_close": 523.41,
  "prev_bar_close": 523.10,
  "prev2_bar_close": 522.80
}
```
