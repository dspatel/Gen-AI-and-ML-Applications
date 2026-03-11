from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path.cwd()
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
STATE_DIR = ROOT / "state"


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        parsed = yaml.safe_load(f)
    return parsed if isinstance(parsed, dict) else {}


def save_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False)


def init_user_config(force: bool = False) -> list[Path]:
    created: list[Path] = []
    mapping = [
        ("profile.example.yaml", "profile.yaml"),
        ("preferences.example.yaml", "preferences.yaml"),
        ("sources.example.yaml", "sources.yaml"),
        ("automation.example.yaml", "automation.yaml"),
    ]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    for src_name, dst_name in mapping:
        src = CONFIG_DIR / src_name
        dst = CONFIG_DIR / dst_name
        if not src.exists():
            continue
        if dst.exists() and not force:
            continue
        shutil.copyfile(src, dst)
        created.append(dst)
    return created


def require_config(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{name}.yaml"
    cfg = load_yaml(path)
    if not cfg:
        raise FileNotFoundError(
            f"Missing or empty config file: {path}. Run `jobapp-agent init` first."
        )
    return cfg
