from __future__ import annotations
from .config_loader import load_config
from .pipeline import run_from_config

def run(config_path: str = "orb_r6_config.yaml") -> None:
    cfg = load_config(config_path)
    run_from_config(cfg)

def main() -> None:
    run()

if __name__ == "__main__":
    main()
