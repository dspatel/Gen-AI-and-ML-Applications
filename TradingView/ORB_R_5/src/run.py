from __future__ import annotations
from .config_loader import load_config
from .pipeline import run_from_config

def main() -> None:
    cfg = load_config("config.yaml")
    run_from_config(cfg)

if __name__ == "__main__":
    main()
