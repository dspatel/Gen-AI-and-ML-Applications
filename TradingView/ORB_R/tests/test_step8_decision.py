
from orb_ref.decision import load_decision_rules, decide

def test_decision_outputs(tmp_path):
    rules = load_decision_rules("config/decision_rules.yml")
    metrics = {
        "inflation_factor": 1.4,
        "median_inside_own_or_pct": 0.5,
        "median_range_to_or": 3.0,
        "bias_consistency": 0.8,
        "close_pen": 0.08,
        "body_norm": 0.06,
    }
    res = decide(metrics, "UP", rules)
    assert res.decision == "LONG"
    assert 0.0 <= res.confidence <= 1.0
    assert len(res.reasons) >= 1
