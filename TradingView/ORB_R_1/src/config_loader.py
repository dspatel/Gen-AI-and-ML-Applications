from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
import yaml

def load_yaml(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_symbols(cfg: Dict[str, Any]) -> List[str]:
    symbols = list(cfg.get("universe", {}).get("symbols", []) or [])
    sym_file = cfg.get("universe", {}).get("symbols_file")
    if sym_file:
        p = Path(sym_file)
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    symbols.append(s)
    # de-dupe preserve order
    out = []
    seen = set()
    for s in symbols:
        s = s.strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
