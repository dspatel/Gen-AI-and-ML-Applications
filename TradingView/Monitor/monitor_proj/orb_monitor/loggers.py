from __future__ import annotations

import csv
from pathlib import Path
from .config import Config
from .strategy import BreakoutEvent

def log_path(cfg: Config, session_date: str) -> Path:
    """Separate logs by mode so TEST never mixes with LIVE."""
    mode = "TEST" if cfg.test_mode else "LIVE"
    base = cfg.output_dir / mode
    base.mkdir(parents=True, exist_ok=True)
    return base / f"breakouts_{session_date}_{cfg.candle_minutes}m.csv"

def append_event(cfg: Config, path: Path, e: BreakoutEvent) -> None:
    """Append a single event to CSV (creates header if file doesn't exist)."""
    header = [
        "session_date", "symbol", "or_high", "or_low", "direction", "is_catchup",
        "breakout_time", "breakout_close", "breakout_volume",
        "confirm_time", "confirm_close", "confirm_volume",
    ]
    file_exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow([
            e.session_date, e.symbol, f"{e.or_high:.4f}", f"{e.or_low:.4f}", e.direction, int(e.is_catchup),
            str(e.breakout_dt)[:19], f"{e.breakout_close:.4f}", e.breakout_volume,
            str(e.confirm_dt)[:19], f"{e.confirm_close:.4f}", e.confirm_volume,
        ])


def notify_debug_path(cfg: Config, session_date: str) -> Path:
    """Notification decision log path (per session/day)."""
    mode = "TEST" if cfg.test_mode else "LIVE"
    base = cfg.output_dir / mode
    base.mkdir(parents=True, exist_ok=True)
    return base / f"notify_{session_date}_{cfg.candle_minutes}m.log"


def append_notify_decision(cfg: Config, session_date: str, symbol: str, kind: str, decision: str, detail: str = "") -> None:
    """Append a line to a simple notification decision log.

    This is intentionally plain-text to keep overhead minimal during live polling.
    """
    try:
        path = notify_debug_path(cfg, session_date)
        msg = f"{symbol} | {kind} | {decision}"
        if detail:
            msg += f" | {detail}"
        msg += "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        # Never let logging interfere with trading logic.
        return
