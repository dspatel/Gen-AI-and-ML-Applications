from __future__ import annotations

from typing import Dict, Literal

Direction = Literal["UP", "DOWN"]


def compute_quality(
    direction: Direction,
    ref_low: float,
    ref_high: float,
    ref_width: float,
    o: float,
    h: float,
    l: float,
    c: float,
) -> Dict[str, float]:
    """Breakout candle quality / nature metrics.

    All metrics are normalized (mostly by ref_width or candle range) so they are comparable across symbols.

    Returned keys (all floats unless noted):
    - close_pen: how far the CLOSE is beyond the breakout level, normalized by ref_width.
    - wick_pen: how far the wick RE-ENTERS the range from the breakout level, normalized by ref_width.
      (Lower is cleaner; 0.0 means no wick back into the range.)
    - body_norm: candle body / candle range (0..1).
    - range_norm: candle range / ref_width.
    - close_pos: where the close sits within the candle's own range (0..1).
    - upper_wick_ratio: upper wick / candle range (0..1).
    - lower_wick_ratio: lower wick / candle range (0..1).
    - clean_break: 1.0 if (close_pen>0 and wick_pen==0) else 0.0.
    """
    eps = 1e-12
    width = float(ref_width) if ref_width and ref_width > 0 else 0.0
    rng = float(h - l)
    body = abs(float(c - o))

    body_norm = (body / rng) if rng > eps else 0.0
    range_norm = (rng / width) if width > eps else 0.0

    # Candle-shape metrics
    close_pos = ((float(c) - float(l)) / rng) if rng > eps else 0.0  # 0..1
    upper_wick = float(h - max(o, c))
    lower_wick = float(min(o, c) - l)
    upper_wick_ratio = (upper_wick / rng) if rng > eps else 0.0
    lower_wick_ratio = (lower_wick / rng) if rng > eps else 0.0

    # Breakout-level penetration and wick-back
    close_pen = 0.0
    wick_pen = 0.0
    if direction == "UP":
        close_pen = (float(c) - float(ref_high)) / width if width > eps else 0.0
        wick_pen = (float(ref_high) - float(l)) / width if (width > eps and l < ref_high) else 0.0
    else:
        close_pen = (float(ref_low) - float(c)) / width if width > eps else 0.0
        wick_pen = (float(h) - float(ref_low)) / width if (width > eps and h > ref_low) else 0.0

    close_pen = max(0.0, close_pen)
    wick_pen = max(0.0, wick_pen)
    clean_break = 1.0 if (close_pen > 0.0 and wick_pen <= 0.0) else 0.0

    return {
        "close_pen": close_pen,
        "wick_pen": wick_pen,
        "body_norm": body_norm,
        "range_norm": range_norm,
        "close_pos": max(0.0, min(1.0, close_pos)),
        "upper_wick_ratio": max(0.0, min(1.0, upper_wick_ratio)),
        "lower_wick_ratio": max(0.0, min(1.0, lower_wick_ratio)),
        "clean_break": clean_break,
    }
