"""Fitter on synthetic curves: every class, with overhead floor and noise."""
import math

from perfmeasure.core.fitting import MEM_FLOOR, fit
from perfmeasure.core.model import CLASSES, Point

OVERHEAD = 1e-7
_CLASS_FN = dict(CLASSES)


def synth(cls: str, coeff: float, sizes: list[int],
          noise: float = 0.05) -> list[Point]:
    fn = _CLASS_FN[cls]
    pts = []
    for i, n in enumerate(sizes):
        wiggle = 1.0 + noise * math.sin(i * 2.3)  # deterministic +-noise
        y = (OVERHEAD + coeff * fn(n)) * wiggle
        pts.append(Point(n=n, seconds=y, reps=3, batched=y < 10e-6))
    return pts


SIZES = [4 * 2 ** k for k in range(18)]        # 4 .. 524288
INT_SIZES = [2, 4, 8, 12, 16, 24, 32]


def test_constant():
    r = fit(synth("O(1)", 5e-8, SIZES))
    assert r.cls == "O(1)"


def test_log():
    r = fit(synth("O(log n)", 3e-8, SIZES))
    assert "O(log n)" in r.candidates
    assert r.cls in ("O(log n)", "O(1)")


def test_linear():
    r = fit(synth("O(n)", 2e-8, SIZES))
    assert "O(n)" in r.candidates


def test_nlogn():
    r = fit(synth("O(n log n)", 2e-8, SIZES))
    assert "O(n log n)" in r.candidates


def test_quadratic():
    r = fit(synth("O(n^2)", 2e-9, SIZES[:12]))
    assert r.cls == "O(n^2)"
    assert "O(n)" not in r.candidates


def test_cubic():
    r = fit(synth("O(n^3)", 2e-10, SIZES[:9]))
    assert r.cls == "O(n^3)"


def test_exponential():
    r = fit(synth("O(2^n)", 1e-9, INT_SIZES))
    assert r.cls == "O(2^n)"


def test_exponential_never_fitted_at_large_n():
    r = fit(synth("O(n)", 2e-8, SIZES))
    assert "O(2^n)" not in r.candidates


def test_linear_with_cache_bend_reports_ambiguity_not_wrong_certainty():
    # superlinear bend from memory hierarchy: last points inflated 40%
    pts = synth("O(n)", 2e-8, SIZES, noise=0.0)
    for p in pts[-4:]:
        p.seconds *= 1.4
    r = fit(pts)
    assert "O(n)" in r.candidates


def test_insufficient_points():
    r = fit(synth("O(n)", 2e-8, SIZES[:3]))
    assert r.cls is None
    assert "insufficient" in r.reason


def test_memory_all_zero_is_constant():
    pts = [Point(n=n, seconds=1e-3, reps=1, peak_bytes=64) for n in SIZES]
    r = fit(pts, value=lambda p: p.peak_bytes, floor=MEM_FLOOR)
    assert r.cls == "O(1)"


def test_memory_linear():
    pts = [Point(n=n, seconds=1e-3, reps=1, peak_bytes=8 * n + 56)
           for n in SIZES]
    r = fit(pts, value=lambda p: p.peak_bytes, floor=MEM_FLOOR)
    assert "O(n)" in r.candidates
