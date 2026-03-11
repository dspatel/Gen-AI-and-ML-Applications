
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional
import pandas as pd


@dataclass(frozen=True)
class BreakoutParams:
    close_required: bool = True
    inside_reset_pct: float = 0.10  # must reset back inside by this fraction of ref_width


def detect_breakouts_close_only(
    session_df: pd.DataFrame,
    ref_low: float,
    ref_high: float,
    params: Optional[BreakoutParams] = None,
) -> List[Dict]:
    """Detect close-only breakouts with simple re-arm.

    State machine:
      - start ARMED
      - when ARMED:
          UP breakout if Close > ref_high
          DOWN breakout if Close < ref_low
        then go to DISARMED(direction)
      - when DISARMED(up): re-arm once Close <= ref_high - reset
      - when DISARMED(down): re-arm once Close >= ref_low + reset

    Returns a list of breakout events with timestamp and direction.
    """
    if session_df is None or session_df.empty:
        return []

    p = params or BreakoutParams()
    width = float(ref_high - ref_low)
    reset = max(0.0, float(p.inside_reset_pct)) * width

    state = "ARMED"
    disarmed_dir = None  # "UP" or "DOWN"
    events: List[Dict] = []

    for ts, bar in session_df.iterrows():
        c = float(bar["Close"])

        if state == "ARMED":
            if p.close_required:
                if c > ref_high:
                    events.append({"timestamp": ts, "direction": "UP"})
                    state = "DISARMED"
                    disarmed_dir = "UP"
                elif c < ref_low:
                    events.append({"timestamp": ts, "direction": "DOWN"})
                    state = "DISARMED"
                    disarmed_dir = "DOWN"
            else:
                # (not used currently) wick-touch mode could be added later
                pass

        else:  # DISARMED
            if disarmed_dir == "UP":
                if c <= (ref_high - reset):
                    state = "ARMED"
                    disarmed_dir = None
            elif disarmed_dir == "DOWN":
                if c >= (ref_low + reset):
                    state = "ARMED"
                    disarmed_dir = None

    return events
