"""Pure-logic tests for M2: probe candidate ordering, memoization
detection, blind-spot demotion, growth-spread guard."""
from perfmeasure.core.fitting import fit
from perfmeasure.core.model import FunctionReport, Point, ShapeResult
from perfmeasure.core.orchestrator import _blindspot_check, _memoized
from perfmeasure.core.probing import candidates_for


def test_probe_name_heuristics_order():
    assert candidates_for("n")[0] == "int_mag"
    assert candidates_for("depth")[0] == "int_mag"
    assert candidates_for("text")[0] == "str_"
    assert candidates_for("xs")[0] == "list_int"
    assert candidates_for("mystery")[0] == "list_int"   # default order
    assert set(candidates_for("n")) >= {"list_int", "str_", "int_mag"}


def test_memoized_detection():
    memo = [Point(n=2 ** k, seconds=1e-8, reps=5, first_seconds=1e-8,
                  warmup_seconds=1e-3 * 2 ** k) for k in range(2, 8)]
    assert _memoized(memo)
    normal = [Point(n=2 ** k, seconds=1e-5, reps=5, first_seconds=1.5e-5,
                    warmup_seconds=2e-5) for k in range(2, 8)]
    assert not _memoized(normal)


def test_blindspot_demotes_confident_o1_space():
    pts = [Point(n=4 * 2 ** k, seconds=1e-3, reps=1, peak_bytes=104,
                 ret_deepsize=64 * 4 * 2 ** k) for k in range(12)]
    shape = ShapeResult(shape="random", points=pts)
    shape.space_fit = fit(pts, value=lambda p: p.peak_bytes, floor=1024.0)
    assert shape.space_fit.cls == "O(1)"
    report = FunctionReport(fid="f", file="f", line=1, provenance="MEASURED")
    _blindspot_check(shape, report)
    assert report.flags.get("untracked_alloc_suspected")
    assert "O(1)" in shape.space_fit.candidates
    assert any(c != "O(1)" for c in shape.space_fit.candidates)


def test_single_spike_cannot_classify():
    pts = [Point(n=4 * 2 ** k, seconds=1e-3, reps=1,
                 peak_bytes=104 if k < 11 else 500_000) for k in range(12)]
    r = fit(pts, value=lambda p: p.peak_bytes, floor=1024.0)
    assert r.cls is None
    assert "cannot classify" in r.reason
