# ORB Monitor – Architecture

## High-level Flow
1. Data Layer (Yahoo intraday candles)
2. Session Layer (market open anchored, catchup-capable)
3. Strategy Layer (OR build by **time window** → breakout → confirm → re-arm)
4. State Layer (SymbolState persisted per run)
5. Notification Layer (Discord, catchup-aware)
6. Logging Layer (CSV logs: OR, breakout, catchup)

## Mental Model
Market Open → Build OR → Breakout → Confirm → Notify → Re-arm → Repeat

## Design Principles
- No resampling
- Candle-close driven
- Deterministic replay
- Production-safe defaults

## Opening Range Windowing

The Opening Range (OR) is built using a timestamp-based window from market open to `open + orb_minutes`, rather than a fixed bar count. This ensures correct behavior across candle sizes (e.g., 15m candles with a 30m OR uses 2 bars). The monitor tracks the number of bars used (`orb_len_bars`) for transparency and debugging.
