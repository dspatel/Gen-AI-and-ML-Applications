from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional
import pandas as pd

from orb_ref.breakouts import BreakoutParams, detect_breakouts_close_only_stepwise


@dataclass
class LadderState:
    # per direction, which horizons broken so far today
    broken_up: List[int]
    broken_down: List[int]
    # per horizon, stepwise detector state
    detector_state: Dict[int, Dict[str, Any]]

    @staticmethod
    def fresh() -> "LadderState":
        return LadderState(broken_up=[], broken_down=[], detector_state={})


def run_ladder_stepwise(
    df: pd.DataFrame,
    refs_by_h: Dict[int, Dict[str, Any]],
    params: BreakoutParams,
    state: Optional[LadderState] = None,
    order: str = "ASC",  # ASC: 3->5->9 when simultaneous
) -> Tuple[List[Dict[str, Any]], LadderState]:
    """Evaluate ladder breakouts across horizons on the provided bars.

    Returns events with fields:
      timestamp, direction, horizon_days, simultaneous_horizons, broken_before
    """
    st = state or LadderState.fresh()
    events_out: List[Dict[str, Any]] = []

    hs = [h for h, ref in refs_by_h.items() if ref]
    hs = sorted(hs) if order.upper() == "ASC" else sorted(hs, reverse=True)

    # Run stepwise detector per horizon over the df (assumes df is incremental chunk; caller can pass 1-bar frames)
    triggered_this_chunk: List[Tuple[int, Dict[str, Any]]] = []
    for h in hs:
        ref = refs_by_h[h]
        dstate = st.detector_state.get(h)
        evs, new_state = detect_breakouts_close_only_stepwise(df, ref_low=float(ref["ref_low"]), ref_high=float(ref["ref_high"]), params=params, state=dstate)
        st.detector_state[h] = new_state
        for e in evs:
            triggered_this_chunk.append((h, e))

    if not triggered_this_chunk:
        return events_out, st

    # group by timestamp
    by_ts: Dict[Any, List[Tuple[int, Dict[str, Any]]]] = {}
    for h, e in triggered_this_chunk:
        by_ts.setdefault(e["timestamp"], []).append((h, e))

    for ts, items in by_ts.items():
        # partition by direction
        by_dir: Dict[str, List[int]] = {"UP": [], "DOWN": []}
        for h, e in items:
            by_dir[e["direction"]].append(h)

        for direction, horizons in by_dir.items():
            if not horizons:
                continue
            horizons_sorted = sorted(horizons) if order.upper() == "ASC" else sorted(horizons, reverse=True)

            if direction == "UP":
                broken_before = list(st.broken_up)
                already = set(st.broken_up)
            else:
                broken_before = list(st.broken_down)
                already = set(st.broken_down)

            simultaneous = ",".join(str(x) for x in horizons_sorted)

            for h in horizons_sorted:
                # If already broken earlier in day for this direction, still record (but mark as already)
                if direction == "UP":
                    if h not in st.broken_up:
                        st.broken_up.append(h)
                else:
                    if h not in st.broken_down:
                        st.broken_down.append(h)

                events_out.append({
                    "timestamp": ts,
                    "direction": direction,
                    "horizon_days": h,
                    "simultaneous_horizons": simultaneous if len(horizons_sorted) > 1 else "",
                    "broken_horizons_before": ",".join(str(x) for x in broken_before),
                    "broken_horizons_after": ",".join(str(x) for x in (st.broken_up if direction=="UP" else st.broken_down)),
                })

    return events_out, st
