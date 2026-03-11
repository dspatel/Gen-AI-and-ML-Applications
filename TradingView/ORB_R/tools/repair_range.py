
import argparse
from datetime import date
from subprocess import run
import yaml

from orb_ref.storage.store_factory import make_store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--mode", choices=["TEST", "PROD"], default=None, help="Override storage.mode")
    ap.add_argument("--delete-only", action="store_true", help="Only delete rows; do not rerun")
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config/config.example.yml", encoding="utf-8"))
    if args.mode:
        cfg.setdefault("storage", {})["mode"] = args.mode

    store = make_store(cfg)

    s = date.fromisoformat(args.start)
    e = date.fromisoformat(args.end)

    d1 = store.delete_date_range("daily_symbol_metrics", s, e)
    d2 = store.delete_date_range("breakout_events", s, e)
    print(f"Deleted daily_symbol_metrics: {d1}, breakout_events: {d2} from {store.db_path}")

    if args.delete_only:
        return

    run(["python", "-m", "tools.backfill", "--start", args.start, "--end", args.end, "--mode", (args.mode or cfg.get("storage", {}).get("mode", "TEST"))], check=True)


if __name__ == "__main__":
    main()
