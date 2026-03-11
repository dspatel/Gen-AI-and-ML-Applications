
from pathlib import Path
from orb_ref.interpretation.label_engine import LabelEngine

here = Path(__file__).resolve().parents[1]
engine = LabelEngine(str(here / "config" / "labels.yml"))

sample_metrics = {
    "or_overlap_ratio": 0.12,
    "inflation_factor": 2.8,
    "median_inside_own_or_pct": 0.38,
    "median_range_to_or": 4.1,
    "mean_direction_bias": 0.10,
    "bias_consistency": 0.60,
    "close_pen": 0.16,
    "wick_pen": 0.22,
    "body_norm": 0.11,
}

labels = engine.build_labels(sample_metrics, use_icons=True)
for k, v in labels.items():
    print(f"{k}: {v}")
