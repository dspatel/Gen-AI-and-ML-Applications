"""Nuke (reset) the SQLite DB safely.

Why:
- If you changed schema (e.g., model.sql / utils/sqlite_store.py), the cleanest workflow is to delete the DB.

What it does:
- Moves the DB file to a timestamped backup next to it
- Removes -wal / -shm files if present

Usage:
  python tools/nuke_db.py
  python tools/nuke_db.py --yes

By default it asks for confirmation unless --yes is provided.
"""

import argparse
import os
import shutil
from datetime import datetime

from config import CFG


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="Do not prompt for confirmation.")
    args = ap.parse_args()

    db_path = CFG.DB_PATH
    wal_path = db_path + "-wal"
    shm_path = db_path + "-shm"

    if not os.path.exists(db_path) and not os.path.exists(wal_path) and not os.path.exists(shm_path):
        print(f"No DB artifacts found at: {db_path} (+ -wal/-shm)")
        return

    if not args.yes:
        resp = input(f"This will RESET the DB at {db_path}. Type 'NUKE' to continue: ").strip().upper()
        if resp != "NUKE":
            print("Aborted.")
            return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(db_path):
        backup = db_path.replace(".sqlite", f"_backup_{ts}.sqlite")
        shutil.move(db_path, backup)
        print(f"Moved DB to: {backup}")

    for p in (wal_path, shm_path):
        if os.path.exists(p):
            os.remove(p)
            print(f"Removed: {p}")

    print("DB reset complete. Next run will recreate schema automatically.")


if __name__ == "__main__":
    main()
