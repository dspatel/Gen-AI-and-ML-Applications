from __future__ import annotations

import csv
from pathlib import Path
from typing import List
from .config_loader import SymbolsConfig

def load_symbols(cfg: SymbolsConfig) -> List[str]:
    mode = (cfg.mode or "list").lower()

    if mode == "single":
        if not cfg.single:
            raise ValueError("symbols.mode=single but symbols.single is empty")
        return [cfg.single.strip().upper()]

    if mode == "list":
        if not cfg.list:
            raise ValueError("symbols.mode=list but symbols.list is empty")
        return [s.strip().upper() for s in cfg.list if s and str(s).strip()]

    if mode == "csv":
        path = Path(cfg.csv_path)
        if not path.exists():
            raise FileNotFoundError(f"Symbols CSV not found: {path.resolve()}")
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and any(h.lower() == "symbol" for h in reader.fieldnames):
                sym_col = next(h for h in reader.fieldnames if h.lower() == "symbol")
                out = [row.get(sym_col, "").strip().upper() for row in reader]
                out = [x for x in out if x]
            else:
                f.seek(0)
                raw = csv.reader(f)
                out = []
                for row in raw:
                    if not row:
                        continue
                    val = (row[0] or "").strip().upper()
                    if val and val != "SYMBOL":
                        out.append(val)
        if not out:
            raise ValueError(f"No symbols found in CSV: {path.resolve()}")
        return out

    raise ValueError(f"Unsupported symbols.mode: {cfg.mode} (expected single|list|csv)")
