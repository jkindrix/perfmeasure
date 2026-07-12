"""The eval infrastructure is the quality gate — it gets the same rigor
it enforces: the README splice must fail loud on missing markers, and
wild.py's regression detection must actually detect."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "evals"))
import harness  # noqa: E402
import wild  # noqa: E402


def test_update_readme_splices_between_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("intro\n<!-- gate:begin -->\nstale\n<!-- gate:end -->\n")
    harness._update_readme(73, 73, 50, 1.5, 20, 20, 7, 7, 120.0,
                           readme=readme)
    text = readme.read_text()
    assert "stale" not in text
    assert "73/73 time classes" in text
    assert text.index("<!-- gate:begin") < text.index("50 exact")


def test_update_readme_fails_loud_without_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("no markers here\n")
    with pytest.raises(SystemExit):
        harness._update_readme(1, 1, 1, 1.0, 1, 1, 1, 1, 1.0, readme=readme)


def test_wild_regression_is_a_structural_drop():
    base = {"functions": 10, "measured": 5, "structural": 5,
            "reasons": {"generic": 2}}
    same = {"functions": 10, "measured": 5, "structural": 5,
            "reasons": {"generic": 2}}
    drop = {"functions": 10, "measured": 4, "structural": 4,
            "reasons": {"generic": 2}}
    gain = {"functions": 12, "measured": 7, "structural": 7,
            "reasons": {"generic": 2}}
    # timing flip: a borderline ladder moved from measured to
    # budget-bound — structural count holds, so the gate must not fail
    flip = {"functions": 10, "measured": 4, "structural": 5,
            "budget_bound": 1, "reasons": {"generic": 2}}
    assert wild.regressions("t", same, base) == []
    assert wild.regressions("t", gain, base) == []
    assert wild.regressions("t", flip, base) == []
    assert wild.regressions("t", drop, base) == [
        "t: structurally drivable 4 < baseline 5"]
    assert wild.regressions("t", drop, None) == []
    # baselines predating the structural field gate on their measured count
    old_base = {"functions": 10, "measured": 5, "reasons": {}}
    assert wild.regressions("t", flip, old_base) == []
    assert wild.regressions("t", drop, old_base) == [
        "t: structurally drivable 4 < baseline 5"]


def test_wild_new_reasons_reported_not_failed():
    base = {"reasons": {"generic": 2}}
    fresh = {"reasons": {"generic": 1, "param 'path'": 3}}
    assert wild.new_reasons(fresh, base) == ["param 'path'"]
    assert wild.new_reasons(fresh, None) == []
