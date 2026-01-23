from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any

try:
    import yaml
except ImportError as e:
    raise RuntimeError("Missing dependency: pyyaml. Install with: pip install pyyaml") from e

def load_symbols(only_enabled: bool = True) -> List[Dict[str, Any]]:
    cfg_path = Path(__file__).parent / "symbols.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    symbols = cfg.get("symbols", [])
    if only_enabled:
        symbols = [s for s in symbols if s.get("enabled", True)]
    for s in symbols:
        s["ticker"] = str(s.get("ticker","")).strip().upper()
    return symbols
