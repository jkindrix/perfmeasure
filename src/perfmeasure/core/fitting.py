"""Curve fitting: measured (n, value) points -> complexity class.

Model: y(n) ~ a + b*f(n), where `a` is the constant call-overhead floor
(estimated from the smallest measurements, not fitted) and `b` is a robust
median scale per candidate class. Scoring happens in LOG space — residuals
log10(y / (a + b*f(n))) are scale-free, so a ladder spanning five decades
weighs every regime equally and the overhead floor at small n cannot drag
the asymptotic fit (the failure mode of plain or relative-weighted least
squares on this data).

Winner = lowest log-RMSE, with a simplicity preference on near-ties.
Candidates (the AMBIGUOUS set) = every class whose log-RMSE is within
ADEQUACY of the winner's — real measurements bend (caches, allocator
steps), so a rival that also explains the data must be reported, not
silently dropped. Guards: constant guard (non-constant needs >= 4x total
growth), O(2^n) only fitted when max n <= 64.

Thresholds are calibrated against evals/harness.py, not vibes.
"""
from __future__ import annotations

import math

from perfmeasure.core.model import CLASS_ORDER, CLASSES, FitResult, Point

MIN_POINTS = 5
MIN_DOUBLINGS = 3
TIME_FLOOR = 50e-6           # single-call timer noise; batched points are finer
MEM_FLOOR = 1024.0
EXP_MAX_N = 64
TIE_BAND = 0.02              # log10-rmse gap treated as a tie -> simpler class
ADEQUACY_ABS = 0.10          # a rival within 10^0.1 (~26%) typical deviation
ADEQUACY_REL = 1.5           # ... or within 1.5x the winner's rmse, is plausible
CONFIDENT_MARGIN = 0.08      # winner..runner-up rmse gap below this -> less sure


def _fit_class(pts: list[tuple[float, float]], fn, floor: float) -> float:
    """Log-RMSE of y ~ floor + b*f(n) with b = robust median scale."""
    signal = [(y - floor) / fn(n) for n, y in pts if y > 2 * floor and fn(n) > 0]
    if signal:
        signal.sort()
        b = signal[len(signal) // 2]
    else:
        b = 0.0
    err = 0.0
    for n, y in pts:
        model = floor + b * fn(n)
        err += math.log10(max(y, 1e-15) / max(model, 1e-15)) ** 2
    return math.sqrt(err / len(pts))


def _growth(pts: list[tuple[float, float]]) -> float:
    return pts[-1][1] / max(pts[0][1], 1e-15)


def monotonicity_violations(values: list[float]) -> int:
    return sum(1 for a, b in zip(values, values[1:]) if b < 0.8 * a)


def fit(points: list[Point], value=lambda p: p.seconds,
        floor: float = TIME_FLOOR) -> FitResult:
    raw = [(p, value(p)) for p in points if value(p) is not None]
    if not raw:
        return FitResult(None, [], None, "no data points")
    pts = sorted((float(p.n), float(v)) for p, v in raw)

    # measurement resolution: everything indistinguishable from zero cost
    if all(v < floor for _, v in pts) and not any(p.batched for p, _ in raw):
        return FitResult("O(1)", ["O(1)"], None,
                         "all values below measurement floor")

    span = math.log2(pts[-1][0] / pts[0][0]) if pts[0][0] > 0 else 0.0
    if len(pts) < MIN_POINTS or span < MIN_DOUBLINGS:
        return FitResult(
            None, [], None,
            f"insufficient range: {len(pts)} points over {span:.1f} doublings"
            f" (need >= {MIN_POINTS} points / {MIN_DOUBLINGS} doublings)")

    # constant guard first: barely-growing data is constant at our resolution
    if _growth(pts) < 2.0:
        return FitResult("O(1)", ["O(1)"], None, "total growth < 2x")

    # overhead floor: the cheapest measured call, slightly deflated
    overhead = 0.95 * min(v for _, v in pts)

    max_n = pts[-1][0]
    scores: list[tuple[float, str]] = []
    for name, fn in CLASSES:
        if name == "O(2^n)" and max_n > EXP_MAX_N:
            continue
        scores.append((_fit_class(pts, fn, overhead), name))
    scores.sort()
    best_score = scores[0][0]

    # simplicity preference on near-ties for the headline class
    tied = [name for s, name in scores if s - best_score <= TIE_BAND]
    winner = min(tied, key=CLASS_ORDER.__getitem__)

    adequacy = max(best_score * ADEQUACY_REL, ADEQUACY_ABS)
    candidates = [name for s, name in scores if s <= adequacy]
    if winner not in candidates:
        candidates.append(winner)

    # non-constant classes need real growth to beat O(1)
    if winner != "O(1)" and _growth(pts) < 4.0 and "O(1)" not in candidates:
        candidates.append("O(1)")

    margin = None
    others = [(s, name) for s, name in scores if name != winner]
    if others:
        winner_score = next(s for s, name in scores if name == winner)
        runner_up_score, runner_up = min(others)
        margin = runner_up_score - winner_score
        # coherence: a margin too small to be confident about is by
        # definition an ambiguity — the runner-up joins the candidate set
        if margin < CONFIDENT_MARGIN and runner_up not in candidates:
            candidates.append(runner_up)

    candidates.sort(key=CLASS_ORDER.__getitem__, reverse=True)
    if len(candidates) > 1:
        return FitResult(winner, candidates, margin, "close fits")
    return FitResult(winner, [winner], margin)
