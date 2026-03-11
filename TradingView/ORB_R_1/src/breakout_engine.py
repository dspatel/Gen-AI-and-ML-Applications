from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

from .intensity import compute_breakout_intensity


@dataclass(frozen=True)
class BreakoutParams:
    close_only: bool = True
    inside_reset_pct: float = 0.10
    min_bars_between_alerts: int = 0
    confirm_closes: int = 1


def _deep_inside(close: float, ref_low: float, ref_high: float, ref_width: float, inside_reset_pct: float) -> bool:
    if ref_width <= 0:
        return ref_low <= close <= ref_high
    margin = inside_reset_pct * ref_width
    return (ref_low + margin) <= close <= (ref_high - margin)


def init_states(horizons: List[int]) -> Dict[int, Dict[str, Any]]:
    """Initialize per-horizon state."""
    return {
        int(h): {
            "armed_up": True,
            "armed_down": True,
            "last_alert_bar_idx": -10**9,
            "confirm_up": 0,
            "confirm_down": 0,
        }
        for h in horizons
    }


def evaluate_bar_multi_horizon(
    bar: pd.Series,
    rr_rows: List[Dict[str, Any]],
    params: BreakoutParams,
    states: Dict[int, Dict[str, Any]],
    bar_idx: int,
) -> List[Dict[str, Any]]:
    """Evaluate a single bar across horizons and return any breakout events for this bar.

    Returns a list of per-horizon events (direction-specific), which can then be combined
    into a single notification by the caller.
    """
    close = float(bar["close"])
    ts = bar.name  # timestamp (tz-aware)

    events: List[Dict[str, Any]] = []

    for rr in rr_rows:
        h = int(rr["horizon_days"])
        if h not in states:
            states[h] = init_states([h])[h]
        st = states[h]

        if not int(rr.get("is_valid", 1)):
            # Invalid RR; skip breakout evaluation
            continue

        ref_high = rr.get("ref_high")
        ref_low = rr.get("ref_low")
        ref_width = rr.get("ref_width")

        if ref_high is None or ref_low is None or ref_width is None:
            continue

        ref_high = float(ref_high)
        ref_low = float(ref_low)
        ref_width = float(ref_width)

        # Re-arm logic (both directions) when price returns deep inside range.
        if _deep_inside(close, ref_low, ref_high, ref_width, params.inside_reset_pct):
            st["armed_up"] = True
            st["armed_down"] = True
            st["confirm_up"] = 0
            st["confirm_down"] = 0

        # Enforce min distance between alerts
        if (bar_idx - int(st["last_alert_bar_idx"])) < int(params.min_bars_between_alerts):
            continue

        # Confirmation counters
        if close > ref_high:
            st["confirm_up"] = int(st.get("confirm_up", 0)) + 1
        else:
            st["confirm_up"] = 0

        if close < ref_low:
            st["confirm_down"] = int(st.get("confirm_down", 0)) + 1
        else:
            st["confirm_down"] = 0

        # Trigger events
        # Up
        if st["armed_up"] and st["confirm_up"] >= int(params.confirm_closes):
            breakout_amt = max(0.0, close - ref_high)
            strength = (breakout_amt / ref_width) if ref_width > 0 else 0.0
            intensity = compute_breakout_intensity(bar, ref_low, ref_high, ref_width, 'DOWN')
            events.append(
                {
                    "timestamp": ts,
                    "horizon_days": h,
                    "direction": "UP",
                    "close": close,
                    "ref_high": ref_high,
                    "ref_low": ref_low,
                    "ref_width": ref_width,
                    "breakout_amt": breakout_amt,
                    "breakout_strength": strength,
                    **intensity,
                }
            )
            st["armed_up"] = False
            st["last_alert_bar_idx"] = bar_idx
            st["confirm_up"] = 0

        # Down
        if st["armed_down"] and st["confirm_down"] >= int(params.confirm_closes):
            breakout_amt = max(0.0, ref_low - close)
            strength = (breakout_amt / ref_width) if ref_width > 0 else 0.0
            intensity = compute_breakout_intensity(bar, ref_low, ref_high, ref_width, 'UP')
            events.append(
                {
                    "timestamp": ts,
                    "horizon_days": h,
                    "direction": "DOWN",
                    "close": close,
                    "ref_high": ref_high,
                    "ref_low": ref_low,
                    "ref_width": ref_width,
                    "breakout_amt": breakout_amt,
                    "breakout_strength": strength,
                    **intensity,
                }
            )
            st["armed_down"] = False
            st["last_alert_bar_idx"] = bar_idx
            st["confirm_down"] = 0

    return events


def choose_primary_event(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Choose primary horizon event deterministically: smallest horizon first."""
    if not events:
        return None
    # If both directions happened (should be rare with close-only), prefer earliest direction? we'll sort by horizon then direction.
    return sorted(events, key=lambda e: (int(e["horizon_days"]), str(e["direction"])))[0]
