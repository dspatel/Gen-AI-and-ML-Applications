"""Print the effective runtime configuration (PRODUCTION).

Production intentionally has no test/replay overrides.
This script is a quick sanity check that paths and key knobs match your config.py.
"""

from __future__ import annotations

from config import CFG


def main() -> None:
    print("=== EMA20 Scanner - Runtime Configuration (PROD) ===")
    print()

    print("--- Storage Paths ---")
    print(f"DB_PATH (LIVE)      : {CFG.DB_PATH}")
    print(f"EOD_DB_PATH         : {CFG.EOD_DB_PATH}")
    print(f"OUTPUT_DIR          : {CFG.OUTPUT_DIR}")
    print(f"SYMBOLS_DIR         : {CFG.SYMBOLS_DIR}")
    print(f"TV_EXPORT_ROOT      : {CFG.TV_EXPORT_ROOT}")
    print()

    print("--- Strategy ---")
    print(f"EMA_PERIOD          : {CFG.EMA_PERIOD}")
    print(f"CROSS_LOOKBACK_DAYS : {CFG.CROSS_LOOKBACK_DAYS}")
    print(f"WINDOW_DAYS_PRIMARY : {CFG.WINDOW_DAYS_PRIMARY}")
    print(f"WINDOW_DAYS_SECONDARY: {CFG.WINDOW_DAYS_SECONDARY} (enabled={CFG.ENABLE_SECONDARY_WINDOW})")
    print(f"ALLOW_ALERT_ON_CROSS_DATE: {CFG.ALLOW_ALERT_ON_CROSS_DATE}")
    print(f"REARM_ON_REENTRY    : {CFG.REARM_ON_REENTRY} (mode={CFG.REENTRY_MODE})")
    print(f"CROSSCOUNT_LOOKBACK_DAYS: {CFG.CROSSCOUNT_LOOKBACK_DAYS}")
    print()

    print("--- Live Tracker ---")
    print(f"TIMEZONE            : {CFG.TIMEZONE}")
    print(f"LIVE_ENABLED        : {CFG.LIVE_ENABLED}")
    print(f"LIVE_INTERVAL       : {CFG.LIVE_INTERVAL}")
    print(f"LIVE_POLL_SECONDS   : {CFG.LIVE_POLL_SECONDS}")
    print(f"LIVE_SESSION_MODE   : {CFG.LIVE_SESSION_MODE}")
    print(f"LIVE_AUTO_WAIT_FOR_SESSION_START: {CFG.LIVE_AUTO_WAIT_FOR_SESSION_START}")
    print(f"LIVE_AUTO_STOP_AFTER_SESSION_END : {CFG.LIVE_AUTO_STOP_AFTER_SESSION_END}")
    print()

    print("--- Discord ---")
    print(f"DISCORD_ENABLED         : {CFG.DISCORD_ENABLED}")
    print(f"DISCORD_SEND_LIVE_ALERTS: {CFG.DISCORD_SEND_LIVE_ALERTS}")
    print(f"DISCORD_SEND_EOD_SUMMARY: {CFG.DISCORD_SEND_EOD_SUMMARY}")
    print(f"DISCORD_SEND_EOD_BANNERS: {CFG.DISCORD_SEND_EOD_BANNERS}")
    print(f"DISCORD_MAX_ALERTS      : {CFG.DISCORD_MAX_ALERTS}")
    if CFG.DISCORD_WEBHOOK_URL:
        masked = CFG.DISCORD_WEBHOOK_URL[:8] + "…" + CFG.DISCORD_WEBHOOK_URL[-6:]
    else:
        masked = "(empty)"
    print(f"DISCORD_WEBHOOK_URL     : {masked}")


if __name__ == "__main__":
    main()
