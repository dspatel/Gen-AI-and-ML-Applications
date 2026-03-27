# Notification Format

## Purpose

Notifications should make it possible to understand both the underlying signal and the chosen option expression.

## Entry Notification

Must include:

- symbol
- direction
- underlying event timestamp
- underlying variant id
- underlying reference metrics
- selected option symbol
- expiration
- strike
- right
- DTE
- delta
- bid
- ask
- spread percent
- contracts
- premium at risk
- planned exit policy

## Exit Notification

Must include:

- symbol
- option symbol
- entry fill
- exit fill
- gross pnl
- net pnl
- return percent
- exit reason
- underlying exit context

## Warning Notifications

Examples:

- no qualifying contract
- spread too wide
- missing Greeks
- partial fill
- stale chain snapshot
- stale position recovery
- forced exit failure
