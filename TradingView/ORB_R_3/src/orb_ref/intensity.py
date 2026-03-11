from __future__ import annotations

from typing import Dict, Any
import pandas as pd

def _get(bar: Any, key_lower: str, key_upper: str) -> float:
    if isinstance(bar, dict):
        if key_lower in bar: return float(bar[key_lower])
        if key_upper in bar: return float(bar[key_upper])
    else:
        if key_lower in bar.index: return float(bar[key_lower])
        if key_upper in bar.index: return float(bar[key_upper])
    raise KeyError(key_lower)

def compute_breakout_intensity(bar: pd.Series, ref_low: float, ref_high: float, ref_width: float, direction: str) -> Dict[str, float]:
    """Compute normalized breakout intensity for a single bar.

    All returned metrics are normalized by ref_width (range width).
    - close_pen: close penetration beyond boundary
    - wick_pen: wick penetration beyond boundary (high for up, low for down)
    - body_norm: candle body size / ref_width
    - range_norm: candle range size / ref_width
    """
    if ref_width <= 0:
        return {"close_pen": 0.0, "wick_pen": 0.0, "body_norm": 0.0, "range_norm": 0.0}

    o = _get(bar, "open", "Open")
    h = _get(bar, "high", "High")
    l = _get(bar, "low", "Low")
    c = _get(bar, "close", "Close")

    body = abs(c - o)
    rng = h - l

    if direction.upper() == "UP":
        close_pen = max(0.0, c - ref_high) / ref_width
        wick_pen = max(0.0, h - ref_high) / ref_width
    else:
        close_pen = max(0.0, ref_low - c) / ref_width
        wick_pen = max(0.0, ref_low - l) / ref_width

    return {
        "close_pen": float(close_pen),
        "wick_pen": float(wick_pen),
        "body_norm": float(body / ref_width),
        "range_norm": float(rng / ref_width),
    }
