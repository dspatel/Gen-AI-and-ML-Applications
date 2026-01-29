#!/usr/bin/env python3
"""Quick DB inspector for the EMA20 Scanner.

Usage:
  python tools/db_inspect.py --db data/cache/marketdata.sqlite --date 2026-01-13
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo


def today_chicago() -> str:
    return datetime.now(tz=ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")


def fetch(conn: sqlite3.Connection, sql: str, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description] if cur.description else []
    rows = cur.fetchall()
    return cols, rows


def print_rows(title: str, cols, rows, limit: int | None = None):
    print(f"\n=== {title} ===")
    if not rows:
        print("(no rows)")
        return
    if limit is not None:
        rows = rows[:limit]
    widths = [len(c) for c in cols]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v)))
    fmt = " | ".join([f"{{:{w}}}" for w in widths])
    print(fmt.format(*cols))
    print("-+-".join(["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*[str(v) for v in r]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/cache/marketdata.sqlite")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (event_date to inspect)")
    args = ap.parse_args()

    date = args.date or today_chicago()
    conn = sqlite3.connect(args.db)

    cols, rows = fetch(conn, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print_rows("Tables", cols, rows)

    cols, rows = fetch(conn, "SELECT MIN(date) AS min_date, MAX(date) AS max_date, COUNT(*) AS rows FROM daily_bars")
    print_rows("daily_bars date range", cols, rows)

    cols, rows = fetch(conn, "SELECT window_days_primary, COUNT(*) AS cnt FROM symbol_state GROUP BY window_days_primary ORDER BY window_days_primary")
    print_rows("symbol_state primary window_days distribution", cols, rows)

    cols, rows = fetch(conn, "SELECT window_days_secondary, COUNT(*) AS cnt FROM symbol_state GROUP BY window_days_secondary ORDER BY window_days_secondary")
    print_rows("symbol_state secondary window_days distribution", cols, rows)

    cols, rows = fetch(conn, """
        SELECT
          SUM(CASE WHEN window_days_primary IS NULL THEN 1 ELSE 0 END) AS missing_days_primary,
          SUM(CASE WHEN window_high_primary IS NULL THEN 1 ELSE 0 END) AS missing_high_primary,
          SUM(CASE WHEN window_low_primary  IS NULL THEN 1 ELSE 0 END) AS missing_low_primary,
          SUM(CASE WHEN window_days_secondary IS NULL THEN 1 ELSE 0 END) AS missing_days_secondary,
          SUM(CASE WHEN window_high_secondary IS NULL THEN 1 ELSE 0 END) AS missing_high_secondary,
          SUM(CASE WHEN window_low_secondary  IS NULL THEN 1 ELSE 0 END) AS missing_low_secondary
        FROM symbol_state
    """)
    print_rows("Missing window fields count", cols, rows)

    cols, rows = fetch(conn, """
        SELECT
          event_date,
          symbol,
          signal,
          source,
          candle_time,
          cross_date,
          window_days_primary,
          window_high_primary,
          window_low_primary,
          ema20,
          ema20_h,
          ema20_l,
          created_at
        FROM alerts_log
        WHERE event_date = ?
        ORDER BY created_at ASC
    """, (date,))
    print_rows(f"alerts_log for {date} (first 50)", cols, rows, limit=50)

    cols, rows = fetch(conn, """
        SELECT event_date, symbol, signal, cross_date, COUNT(*) AS cnt
        FROM alerts_log
        GROUP BY event_date, symbol, signal, cross_date
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
    """)
    print_rows("Potential duplicates (should be empty)", cols, rows)

    cols, rows = fetch(conn, "SELECT source, COUNT(*) AS cnt FROM alerts_log GROUP BY source ORDER BY cnt DESC")
    print_rows("Alert counts by source", cols, rows)

    conn.close()


if __name__ == "__main__":
    main()
