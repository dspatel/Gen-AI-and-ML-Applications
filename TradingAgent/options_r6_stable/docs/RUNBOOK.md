# Runbook

## Current Status

This module has a working scaffold:

- config loader
- isolated DB schema initializer
- ORB-account Alpaca doctor
- deterministic contract-selection demo

It does not yet have:

- research backtester
- paper-trading order engine
- live notifications
- end-of-day reporting

## Intended Operating Model

- separate Alpaca paper account usage through the ORB account keys
- separate options DB
- separate notifications
- separate reports

## Planned One-Time Setup

1. Credentials
   - use `ORB_ALPACA_API_KEY`
   - use `ORB_ALPACA_SECRET_KEY`
   - use `ORB_ALPACA_BASE_URL`
2. Install dependencies
3. Verify stock and option data entitlements
4. Verify option paper-trading permissions
5. Verify options chain and quote retrieval

## Planned Daily Operating Loop

1. Start module
2. Wait for session open
3. Build underlying signal state
4. Evaluate eligible symbols
5. Pull option chain only when a valid underlying event appears
6. Select contract
7. Submit paper order
8. Manage exits
9. Force exit before close
10. Produce end-of-day reports

## Planned Restart Rules

The live runner must handle:

- restart before session
- restart during session
- restart after session
- stale open positions from prior day
- stale open orders from prior day
- partially filled entry or exit orders

## Manual Investigation Checklist

When something looks wrong, inspect in this order:

1. underlying signal event
2. chain snapshot timing
3. contract filter rejections
4. selected contract row
5. position lifecycle row
6. trade exit reason
7. broker open orders / positions
8. EOD report

## Planned Safety Rules

- do not hold through expiration
- do not trade contracts failing spread filters
- do not allow silent fallback to stale data
- do not allow a position without a logged exit policy
- do not treat missing liquidity as a trade
