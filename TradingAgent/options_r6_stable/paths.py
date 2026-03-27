from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PACKAGE_ROOT.parent


def resolve_workspace_path(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return (WORKSPACE_ROOT / p).resolve()
