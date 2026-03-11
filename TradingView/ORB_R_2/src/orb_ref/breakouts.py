from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any
import pandas as pd

@dataclass(frozen=True)
class BreakoutParams:
    close_required: bool = True
    inside_reset_pct: float = 0.10
    confirm_closes: int = 1
    min_bars_between_alerts: int = 0

def _get_close(bar: Any) -> float:
    # bar can be Series or dict-like
    if isinstance(bar, dict):
        if "close" in bar: return float(bar["close"])
        if "Close" in bar: return float(bar["Close"])
    else:
        if "close" in bar.index: return float(bar["close"])
        if "Close" in bar.index: return float(bar["Close"])
    raise KeyError("close")

def detect_breakouts_close_only(
    session_df: pd.DataFrame,
    ref_low: float,
    ref_high: float,
    params: Optional[BreakoutParams] = None,
) -> List[Dict]:
    """Stateless convenience wrapper: runs stepwise detector across full session."""
    events, _ = detect_breakouts_close_only_stepwise(session_df, ref_low, ref_high, params=params, state=None)
    return events

def detect_breakouts_close_only_stepwise(
    session_df: pd.DataFrame,
    ref_low: float,
    ref_high: float,
    params: Optional[BreakoutParams] = None,
    state: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict], Dict[str, Any]]:
    """Stepwise close-only breakout detector with re-arm + confirmation candles.

    State supports incremental replay/live evaluation.
    """
    if session_df is None or session_df.empty:
        return [], state or {"mode":"ARMED", "dir":None, "confirm":0, "last_alert_i":-10**9}

    p = params or BreakoutParams()
    width = float(ref_high - ref_low)
    reset = max(0.0, float(p.inside_reset_pct)) * width
    confirm_n = max(1, int(p.confirm_closes))
    min_gap = max(0, int(p.min_bars_between_alerts))

    st = state.copy() if state else {"mode":"ARMED", "dir":None, "confirm":0, "last_alert_i":-10**9}
    events: List[Dict] = []

    for i, (ts, bar) in enumerate(session_df.iterrows()):
        c = _get_close(bar)

        # enforce minimum gap between alerts
        if i - int(st.get("last_alert_i", -10**9)) < min_gap:
            # still allow rearm logic, but don't fire new events
            pass

        if st["mode"] == "ARMED":
            if c > ref_high:
                st["dir"] = "UP"
                st["confirm"] = int(st.get("confirm", 0)) + 1
            elif c < ref_low:
                st["dir"] = "DOWN"
                st["confirm"] = int(st.get("confirm", 0)) + 1
            else:
                st["dir"] = None
                st["confirm"] = 0

            if st["dir"] and st["confirm"] >= confirm_n and (i - int(st.get("last_alert_i",-10**9)) >= min_gap):
                events.append({"timestamp": ts, "direction": st["dir"]})
                st["mode"] = "DISARMED"
                st["last_alert_i"] = i
                # keep dir in state for rearm decision
        else:  # DISARMED
            disarmed_dir = st.get("dir")
            if disarmed_dir == "UP":
                if c <= (ref_high - reset):
                    st["mode"] = "ARMED"
                    st["dir"] = None
                    st["confirm"] = 0
            elif disarmed_dir == "DOWN":
                if c >= (ref_low + reset):
                    st["mode"] = "ARMED"
                    st["dir"] = None
                    st["confirm"] = 0
            else:
                # safety
                st["mode"] = "ARMED"
                st["dir"] = None
                st["confirm"] = 0

    return events, st
