from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Label:
    emoji: str
    text: str


def _v(x: Optional[float]) -> float:
    return float(x) if x is not None else 0.0


def label_overlap(or_overlap_pairs_pct: Optional[float], or_overlap_days_pct: Optional[float]) -> Label:
    p = _v(or_overlap_pairs_pct); d = _v(or_overlap_days_pct)
    if p >= 0.60 or d >= 0.60:
        return Label("🧩", "ORs clustered")
    if p <= 0.30 and d <= 0.40:
        return Label("🌀", "ORs shifting")
    return Label("🧷", "ORs mixed")


def label_inflation(inflation_factor: Optional[float]) -> Label:
    f = _v(inflation_factor)
    if f <= 0:
        return Label("❓", "inflation n/a")
    if f < 1.25:
        return Label("🧊", "tight RR")
    if f <= 2.25:
        return Label("⚖️", "balanced RR")
    return Label("🔥", "stretched RR")


def label_bias(mean_direction_bias: Optional[float], bias_consistency: Optional[float]) -> Label:
    b = _v(mean_direction_bias); c = _v(bias_consistency)
    if c < 0.60 or abs(b) < 0.05:
        return Label("🟡", "balanced bias")
    return Label("🟢", "up-lean") if b > 0 else Label("🔴", "down-lean")


def label_quality(breakout_strength: Optional[float], wick_pen: Optional[float], body_norm: Optional[float]) -> Tuple[Label, Label, Label]:
    s = _v(breakout_strength); w = _v(wick_pen); body = _v(body_norm)
    if s >= 0.15:
        s_lbl = Label("✅", "clean break")
    elif s >= 0.08:
        s_lbl = Label("🟨", "moderate break")
    else:
        s_lbl = Label("⚠️", "weak break")

    if w >= 0.40:
        w_lbl = Label("🪶", "wicky")
    elif 0 < w <= 0.20:
        w_lbl = Label("🧱", "tight wick")
    else:
        w_lbl = Label("🪵", "some wick")

    if body >= 0.55:
        b_lbl = Label("💪", "strong body")
    elif body <= 0.35:
        b_lbl = Label("🫥", "thin body")
    else:
        b_lbl = Label("🙂", "ok body")
    return s_lbl, w_lbl, b_lbl
