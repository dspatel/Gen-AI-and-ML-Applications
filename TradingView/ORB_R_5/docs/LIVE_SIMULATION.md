# Live run behavior

This project supports a live polling tracker implemented in `src.live`.

## Start-anytime behavior
- If started on a non-session day: exits with a clear message.
- If started before session open: waits until open.
- If started during the session: catches up on completed candles since open and processes them in order.
- If started after close: exits.

## RR phase switching (pre-OR vs post-OR)
- Before OR is complete, breakouts are evaluated using RR with `include_today_or=0` (prior sessions only).
- After the OR window completes, the tracker refreshes RR with `include_today_or=1` and the engine automatically switches to post-OR RR for subsequent bars.

## Heartbeat
- Emits a heartbeat every 60 seconds with phase and last processed candle close timestamp per symbol.
