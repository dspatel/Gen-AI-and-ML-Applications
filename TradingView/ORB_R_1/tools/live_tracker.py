from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.config_loader import load_yaml, load_symbols
from src.calendar import TradingCalendar

def main() -> None:
    ap = argparse.ArgumentParser(description="Live tracker (scaffold): detects start state and prints what it would do.")
    ap.add_argument("--config", default="config/config.yaml")
    args = ap.parse_args()

    cfg = load_yaml(args.config)
    tz = str(cfg.get("market", {}).get("timezone", "America/Chicago"))
    cal = TradingCalendar(exchange=str(cfg.get("market", {}).get("exchange", "XNYS")), timezone=tz)
    now = datetime.now(ZoneInfo(tz))

    today = now.date()
    is_session = cal.is_session(today)

    if not is_session:
        print(f"{today} is NOT a session day. Next step: find next session and wait/exit (configurable).")
        return

    sess = cal.session_times(today)
    if now < sess.open_ts:
        print(f"Pre-market: now={now.isoformat()} open={sess.open_ts.isoformat()} -> wait until open.")
        return
    if sess.open_ts <= now <= sess.close_ts:
        print(f"Intraday: now={now.isoformat()} -> do catch-up then go live. (Not implemented yet)")
        return
    print(f"After-hours: now={now.isoformat()} close={sess.close_ts.isoformat()} -> EOD summary or wait for next session.")

if __name__ == "__main__":
    main()
