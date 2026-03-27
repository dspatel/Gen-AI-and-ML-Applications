from __future__ import annotations

import csv

from .config_loader import SymbolsConfig
from .paths import resolve_workspace_path


def load_symbols(cfg: SymbolsConfig) -> list[str]:
    path = resolve_workspace_path(cfg.csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Symbols CSV not found: {path}")
    out: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "symbol" not in (reader.fieldnames or []):
            raise ValueError("symbols.csv must include a 'symbol' column")
        for row in reader:
            symbol = str((row or {}).get("symbol") or "").strip().upper()
            if symbol:
                out.append(symbol)
    if not out:
        raise ValueError(f"No symbols found in {path}")
    return out
