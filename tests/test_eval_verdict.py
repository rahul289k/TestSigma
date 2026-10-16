import yaml

from triangulate.config import ROOT
from triangulate.eval.harness import verdict

TH = yaml.safe_load((ROOT / "eval" / "thresholds.yaml").read_text())


def result(**kw):
    res = {"invariants": [{"invariant": "quotes_verbatim", "ok": True, "detail": ""}],
           "dom_code": {"precision_by_method": {"i18n_route_unique": 1.0}, "precision_overall": 0.69},
           "req_ui": {"precision": 0.84, "recall": 0.76},
           "prs": [{"pr": 6706, "ran": True, "false_alarms": [], "overall_ok": None, "overall_risk": "Medium", "expected_overall": None}]}
    res.update(kw)
    return res


def test_the_real_runs_scores_are_correct():
    assert verdict(result(), TH) == {"correct": True, "failed": []}


def test_each_failure_is_named():
    bad = result(invariants=[{"invariant": "flows_replayed", "ok": False, "detail": "2 flows failed"}],
                 req_ui={"precision": 0.5, "recall": None},
                 prs=[{"pr": 6584, "ran": True, "false_alarms": ["Copy"], "overall_ok": False, "overall_risk": "High",
                       "expected_overall": "Low"},
                      {"pr": 6707, "ran": False}])
    v = verdict(bad, TH)
    assert not v["correct"]
    joined = " | ".join(v["failed"])
    for part in ("invariant flows_replayed", "G2 precision 0.5 < 0.75", "G2 recall None", "PR 6584: 1 false alarms",
                 "PR 6584: overall risk High != Low", "PR 6707 has no report"):
        assert part in joined
