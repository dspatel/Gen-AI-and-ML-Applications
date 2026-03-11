import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


import argparse
from datetime import date, timedelta
from subprocess import run
import yaml

from orb_ref.sessions import TradingSessions


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--mode", choices=["TEST", "PROD"], default=None, help="Override storage.mode")
    ap.add_argument("--include-nontrading", action="store_true", help="Also run on weekends/holidays (not recommended)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config/config.example.yml", encoding="utf-8"))
    if args.mode:
        cfg.setdefault("storage", {})["mode"] = args.mode

    ts = TradingSessions(
        exchange=cfg.get("market", {}).get("exchange", "XNYS"),
        tz=cfg.get("market", {}).get("timezone", "America/Chicago"),
    )

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    # Keep a pristine copy to restore
    original_cfg_text = open("config/config.example.yml", encoding="utf-8").read()

    try:
        for d in daterange(start, end):
            if not args.include_nontrading and (not ts.is_trading_day(d)):
                continue

            cfg["run"]["asof_date"] = d.isoformat()
            tmp_path = Path("config/_temp_backfill.yml")
            tmp_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

            # Swap config for demo scripts (simple + explicit)
            Path("config/config.example.yml").write_text(tmp_path.read_text(encoding="utf-8"), encoding="utf-8")

            print("Backfill date:", d.isoformat())
            run(["python", "-m", "tools.demo_step5"], check=True)
            run(["python", "-m", "tools.demo_step6"], check=True)

        print("Backfill complete.")
    finally:
        Path("config/config.example.yml").write_text(original_cfg_text, encoding="utf-8")
        try:
            Path("config/_temp_backfill.yml").unlink()
        except Exception:
            pass


if __name__ == "__main__":
    from pathlib import Path
    main()
