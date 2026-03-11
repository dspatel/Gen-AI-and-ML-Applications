
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
    return SQLiteStore(StoreConfig(db_path=resolve_db_path(cfg)))
