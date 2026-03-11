"""Quick webhook sanity test.

Usage:
  python -m tools.test_webhook --title "ORB_REF" --message "hello"
Reads config/config.example.yml.
"""

import argparse
import yaml

from orb_ref.notifier import build_notifier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="ORB_REF")
    ap.add_argument("--message", default="test message")
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config/config.example.yml", encoding="utf-8"))
    notifier = build_notifier(cfg)
    status, body = notifier.send(args.title, args.message)
    print(f"status={status}")
    if body:
        print(body[:800])


if __name__ == "__main__":
    main()
