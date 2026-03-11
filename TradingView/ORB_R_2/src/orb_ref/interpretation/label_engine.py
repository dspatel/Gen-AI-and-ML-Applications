
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
import yaml


@dataclass(frozen=True)
class Label:
    key: str
    label: str
    icon: str = ""
    note: str = ""

    def render(self, use_icons: bool = True) -> str:
        return f"{self.icon} {self.label}".strip() if (use_icons and self.icon) else self.label


class LabelEngine:
    """Config-driven intuition labels (icons optional)."""

    def __init__(self, labels_yml_path: str):
        with open(labels_yml_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f) or {}

    def build_labels(self, metrics: Dict[str, Any], use_icons: bool = True) -> Dict[str, str]:
        return {
            "open_alignment": self._bin_label("open_alignment", metrics).render(use_icons),
            "reference_shape": self._bin_label("reference_shape", metrics).render(use_icons),
            "regime": self._rule_label("regime", metrics).render(use_icons),
            "direction_bias": self._rule_label("direction_bias", metrics).render(use_icons),
            "breakout_strength": self._rule_label("breakout_strength", metrics).render(use_icons),
        }

    def _bin_label(self, group: str, metrics: Dict[str, Any]) -> Label:
        g = self.cfg[group]
        metric_name = g["metric"]
        v = float(metrics.get(metric_name, 0.0))
        for b in g["bins"]:
            if v <= float(b["max"]):
                return Label(group, b["label"], b.get("icon",""), b.get("note",""))
        b = g["bins"][-1]
        return Label(group, b["label"], b.get("icon",""), b.get("note",""))

    def _rule_label(self, group: str, metrics: Dict[str, Any]) -> Label:
        g = self.cfg[group]
        for r in g["rules"]:
            when = r.get("when", {}) or {}
            if self._matches(when, metrics):
                return Label(group, r["label"], r.get("icon",""), r.get("note",""))
        r = g["rules"][-1]
        return Label(group, r["label"], r.get("icon",""), r.get("note",""))

    @staticmethod
    def _matches(when: Dict[str, Any], m: Dict[str, Any]) -> bool:
        def f(x): 
            return None if x is None else float(x)

        inside = f(m.get("median_inside_own_or_pct"))
        rto = f(m.get("median_range_to_or"))
        bias = f(m.get("mean_direction_bias"))
        cons = f(m.get("bias_consistency"))
        close_pen = f(m.get("close_pen"))
        body_norm = f(m.get("body_norm"))

        for k, th in when.items():
            th = float(th)
            if k == "inside_min" and not (inside is not None and inside >= th): return False
            if k == "inside_max" and not (inside is not None and inside <= th): return False
            if k == "range_to_or_min" and not (rto is not None and rto >= th): return False
            if k == "range_to_or_max" and not (rto is not None and rto <= th): return False

            if k == "bias_min" and not (bias is not None and bias >= th): return False
            if k == "bias_max" and not (bias is not None and bias <= th): return False
            if k == "consistency_min" and not (cons is not None and cons >= th): return False

            if k == "close_pen_min" and not (close_pen is not None and close_pen >= th): return False
            if k == "close_pen_max" and not (close_pen is not None and close_pen <= th): return False
            if k == "body_norm_min" and not (body_norm is not None and body_norm >= th): return False
        return True
