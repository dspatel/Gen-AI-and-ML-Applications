from __future__ import annotations

import csv
from pathlib import Path

from .config_loader import SymbolsConfig


def load_symbols(cfg: SymbolsConfig) -> list[str]:
    mode = (cfg.mode or "csv").lower()

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
                col = next(h for h in reader.fieldnames if h.lower() == "symbol")
                symbols = [str(r.get(col, "")).strip().upper() for r in reader]
                symbols = [s for s in symbols if s]
            else:
                f.seek(0)
                raw = csv.reader(f)
                symbols = []
                for row in raw:
                    if not row:
                        continue
                    val = str(row[0]).strip().upper()
                    if val and val != "SYMBOL":
                        symbols.append(val)
        if not symbols:
            raise ValueError(f"No symbols found in CSV: {path.resolve()}")
        return symbols

    raise ValueError(f"Unsupported symbols.mode: {cfg.mode} (expected single|list|csv)")

