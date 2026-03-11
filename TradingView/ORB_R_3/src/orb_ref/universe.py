
from __future__ import annotations

from pathlib import Path
import csv
from typing import Iterable


def _clean_symbol(s: str) -> str:
    return s.strip().upper()


def load_symbols(cfg: dict) -> list[str]:
    """Load symbols from config.

    Priority:
    1) universe.symbols_file if present and exists (txt or csv)
    2) universe.symbols list
    """
    u = cfg.get("universe", {}) or {}
    f = u.get("symbols_file")
    if f:
        path = Path(f)
        if path.exists():
            if path.suffix.lower() == ".csv":
                out: list[str] = []
                with open(path, newline="", encoding="utf-8") as fh:
                    for row in csv.reader(fh):
                        if not row:
                            continue
                        sym = _clean_symbol(row[0])
                        if sym:
                            out.append(sym)
                return sorted(set(out))
            else:
                out = []
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    out.append(_clean_symbol(line))
                return sorted(set(out))

    syms = [_clean_symbol(s) for s in (u.get("symbols") or []) if str(s).strip()]
    return sorted(set(syms))
