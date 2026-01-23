from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class SymbolRow:
    symbol: str
    group: str
    enabled: bool
    notes: str = ""


def _normalize_bool(x) -> bool:
    if isinstance(x, bool):
        return x
    if x is None:
        return False
    s = str(x).strip().lower()
    return s in {"true", "t", "1", "yes", "y", "enabled", "on"}


def load_symbols_csv(
    csv_path: str,
    enabled_only: bool = True,
    allowed_groups: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Loads symbols from a CSV file with columns:
      symbol, group, enabled, (optional) notes

    Returns a cleaned DataFrame:
      symbol (upper), group (lower), enabled (bool), notes (str)
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"symbols CSV not found: {csv_path}")

    df = pd.read_csv(csv_path)

    required = {"symbol", "group", "enabled"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"symbols CSV missing required columns: {sorted(missing)}")

    # Clean
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["group"] = df["group"].astype(str).str.strip().str.lower()
    df["enabled"] = df["enabled"].apply(_normalize_bool)

    if "notes" not in df.columns:
        df["notes"] = ""
    df["notes"] = df["notes"].fillna("").astype(str)

    # Drop empty symbols
    df = df[df["symbol"] != ""].copy()

    # De-duplicate symbols (keep first occurrence)
    df = df.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)

    # Optional filters
    if allowed_groups is not None:
        allowed = {g.strip().lower() for g in allowed_groups}
        df = df[df["group"].isin(allowed)].copy()

    if enabled_only:
        df = df[df["enabled"]].copy()

    # Sort (group then symbol) for stable output
    df = df.sort_values(["group", "symbol"]).reset_index(drop=True)

    return df


def summarize_symbols(df: pd.DataFrame) -> Dict[str, object]:
    """
    Returns basic counts for logging/testing.
    """
    total = int(len(df))
    by_group = df["group"].value_counts().to_dict() if not df.empty else {}
    symbols = df["symbol"].tolist() if not df.empty else []
    return {
        "total": total,
        "by_group": by_group,
        "symbols": symbols,
    }


def load_enabled_symbols(
    csv_path: str,
    allowed_groups: Optional[List[str]] = None,
) -> List[str]:
    df = load_symbols_csv(csv_path, enabled_only=True, allowed_groups=allowed_groups)
    return df["symbol"].tolist()
