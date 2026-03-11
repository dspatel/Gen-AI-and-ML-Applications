
from __future__ import annotations

from pathlib import Path
from orb_ref.storage.sqlite_store import SQLiteStore, StoreConfig

def resolve_db_path(cfg: dict) -> str:
    s = cfg.get("storage", {}) or {}
    db_dir = Path(s.get("db_dir", "db"))
    mode = (s.get("mode", "TEST") or "TEST").upper()
    prod = s.get("prod_db_name", "orb_ref_prod.sqlite")
    test = s.get("test_db_name", "orb_ref_test.sqlite")
    name = prod if mode == "PROD" else test
    return str(db_dir / name)

def make_store(cfg: dict) -> SQLiteStore:
    db_path = Path(resolve_db_path(cfg))
    # UX: make DB location obvious in terminal output.
    # Users frequently expect to *see* a `db/` folder under the repo. But if they run
    # from a different CWD, the relative path can resolve elsewhere. Printing the
    # resolved path removes ambiguity.
    try:
        print(f"[storage] sqlite db: {db_path.resolve()}")
    except Exception:
        print(f"[storage] sqlite db: {db_path}")

    return SQLiteStore(StoreConfig(db_path=str(db_path)))
