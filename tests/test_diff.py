"""Diff gate: one-sided, ambiguity-robust regression detection."""
from perfmeasure.core.diff import diff_reports
from perfmeasure.core.model import FunctionReport


def _base(fid, tcls, tcands=None, prov="MEASURED"):
    return {"function": {"fid": fid}, "provenance": prov,
            "time": {"cls": tcls, "candidates": tcands or [tcls]},
            "space": {"cls": None, "candidates": []}}


def _rep(fid, tcls, tcands=None, prov="MEASURED"):
    return FunctionReport(fid=fid, file="f.py", line=1, provenance=prov,
                          time_cls=tcls,
                          time_candidates=tcands or ([tcls] if tcls else []))


def test_clean_class_is_ok():
    r = diff_reports([_rep("f::a", "O(n)")], [_base("f::a", "O(n)")])
    assert not r["regressions"] and not r["warnings"]
    assert r["compared_ok"] == 1


def test_hard_regression_fails():
    r = diff_reports([_rep("f::a", "O(n^2)")], [_base("f::a", "O(n)")])
    assert len(r["regressions"]) == 1
    assert "O(n) -> O(n^2)" in r["regressions"][0]


def test_overlapping_ambiguity_only_warns():
    # widened to {n log n, n}: most charitable reading still meets baseline
    r = diff_reports([_rep("f::a", "O(n log n)", ["O(n log n)", "O(n)"])],
                     [_base("f::a", "O(n)")])
    assert not r["regressions"]
    assert len(r["warnings"]) == 1


def test_regression_vs_ambiguous_baseline_uses_old_worst():
    # old {n log n, n}: new clean n log n is within the old worst case
    r = diff_reports([_rep("f::a", "O(n log n)")],
                     [_base("f::a", "O(n)", ["O(n log n)", "O(n)"])])
    assert not r["regressions"] and not r["warnings"]


def test_drivability_loss_warns():
    r = diff_reports([_rep("f::a", None, prov="UNDRIVABLE")],
                     [_base("f::a", "O(n)")])
    assert not r["regressions"]
    assert "UNDRIVABLE" in r["warnings"][0]


def test_vanished_function_warns():
    r = diff_reports([], [_base("f::a", "O(n)")])
    assert "not measured now" in r["warnings"][0]


def test_new_function_listed():
    r = diff_reports([_rep("f::b", "O(n)")], [_base("f::a", "O(n)")])
    assert r["new_functions"] == ["f::b"]
