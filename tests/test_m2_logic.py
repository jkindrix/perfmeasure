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


def _aggregated(stop_reason, secs, tmo_n=None):
    from perfmeasure.core.ladder import Budget
    from perfmeasure.core.orchestrator import _Run, _aggregate
    pts = [Point(n=4 * 2 ** k, seconds=s, reps=1)
           for k, s in enumerate(secs)]
    shape = ShapeResult(shape="random", points=pts, stop_reason=stop_reason)
    shape.time_fit = fit(pts)
    if tmo_n is not None:
        shape.failures.append(
            {"n": tmo_n, "kind": "timeout_hard", "message": "killed"})
    report = FunctionReport(fid="f", file="f", line=1, provenance="MEASURED")
    report.per_shape = [shape]
    report.max_n_reached = max(p.n for p in pts)
    _aggregate(report, _Run(shapes=[shape]), probed=False, budget=Budget())
    return report


def test_budget_truncated_o1_is_flagged_and_demoted():
    flat = [2.9e-3] * 8                       # big constant, no growth seen
    r = _aggregated("projected_cost", flat)
    assert r.time_cls == "O(1)"
    assert r.flags.get("constant_within_budget_window") == r.max_n_reached
    assert r.confidence != "high"
    # same data, but the ladder genuinely ran out of sizes: no flag
    r2 = _aggregated("n_max", flat)
    assert "constant_within_budget_window" not in r2.flags


def test_timeout_defying_fit_is_flagged():
    linear = [2e-8 * 4 * 2 ** k for k in range(14)]      # clean O(n)
    r = _aggregated("timeout_hard", linear, tmo_n=4 * 2 ** 14)
    assert r.flags.get("timeout_above_window") == 4 * 2 ** 14
    # a quadratic that honestly outgrew the hard timeout predicts the
    # kill from its own curve: no flag
    quad = [1e-9 * (4 * 2 ** k) ** 2 for k in range(15)]  # last ~1.07s
    r2 = _aggregated("timeout_hard", quad, tmo_n=4 * 2 ** 15)
    assert r2.time_cls == "O(n^2)"
    assert "timeout_above_window" not in r2.flags
