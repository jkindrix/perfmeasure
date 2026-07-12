"""Curve fitting: measured (n, value) points -> complexity class.

Model: y(n) ~ a + b*f(n), where `a` is the constant call-overhead floor
(estimated from the smallest measurements, not fitted) and `b` is the
geometric mean of the per-point ratios (y-a)/f(n) — the closed-form
minimizer of the squared log residuals the score actually uses. Scoring
happens in LOG space — residuals log10(y / (a + b*f(n))) are scale-free,
so a ladder spanning five decades weighs every regime equally and the
overhead floor at small n cannot drag the asymptotic fit (the failure
mode of plain or relative-weighted least squares on this data).

Winner = lowest log-RMSE, with a simplicity preference on near-ties.
Candidates (the AMBIGUOUS set) = every class whose log-RMSE is within
ADEQUACY of the winner's — real measurements bend (caches, allocator
steps), so a rival that also explains the data must be reported, not
silently dropped. The adequacy band is an ABSOLUTE instrument-noise
band, deliberately not the plan's original dAICc<=2 criterion: with
equal-parameter models dAICc reduces to an rmse ratio of e^(1/n) —
~15% on a 7-point ladder — which assumes iid Gaussian residuals that
wall-time data (systematically cache-bent) does not have. Guards:
constant guard (non-constant needs >= 4x total growth), O(2^n) only
fitted when max n <= 64, and a residual-trend check: winner residuals
drifting UP with n mean a hidden slow factor (the classic n-vs-n·log·n
miss), so the next class up joins the candidates. Downward drift is
covered by the tail cross-check instead.

Thresholds are calibrated against evals/harness.py, not vibes.
"""
from __future__ import annotations

import math

from perfmeasure.core.model import CLASS_ORDER, CLASSES, FitResult, Point

MIN_POINTS = 5
MIN_DOUBLINGS = 3
TIME_FLOOR = 50e-6           # single-call timer noise; batched points are finer
MEM_FLOOR = 1024.0
OPS_FLOOR = 100.0            # interpreter call overhead in instructions
EXP_MAX_N = 64
TIE_BAND = 0.02              # log10-rmse gap treated as a tie -> simpler class
ADEQUACY_ABS = 0.10          # a rival within 10^0.1 (~26%) typical deviation
ADEQUACY_REL = 1.5           # ... or within 1.5x the winner's rmse, is plausible
CONFIDENT_MARGIN = 0.08      # winner..runner-up rmse gap below this -> less sure
FLAT_SPREAD = 1.3            # within-segment max/min for a "flat" scale run
STEP_MIN = 1.4               # between-segment jump that reads as a real step
STEP_NOTE = "coefficient step"


def _fit_class(pts: list[tuple[float, float]], fn,
               floor: float) -> tuple[float, float]:
    """(log-RMSE, b) of y ~ floor + b*f(n). b = geometric mean of the
    per-point ratios: the closed-form minimizer of squared log residuals
    of (y - floor) against b*f(n), so the estimator matches the objective
    the score reports (a median would minimize absolute log error)."""
    signal = [(y - floor) / fn(n) for n, y in pts if y > 2 * floor and fn(n) > 0]
    if signal:
        b = math.exp(sum(math.log(s) for s in signal) / len(signal))
    else:
        b = 0.0
    err = 0.0
    for n, y in pts:
        model = floor + b * fn(n)
        err += math.log10(max(y, 1e-15) / max(model, 1e-15)) ** 2
    return math.sqrt(err / len(pts)), b


def _growth(pts: list[tuple[float, float]]) -> float:
    """Total growth, endpoint-noise-robust: geometric mean of the two
    smallest-n values against the two largest-n — one high-variance first
    point must not deflate growth below the constant guard."""
    if len(pts) < 4:
        # fewer than two disjoint pairs: plain endpoint ratio (the 2+2
        # geometric means would share points and read ~1.0 regardless)
        return pts[-1][1] / max(pts[0][1], 1e-15)
    lo = math.sqrt(max(pts[0][1], 1e-15) * max(pts[1][1], 1e-15))
    hi = math.sqrt(max(pts[-1][1], 1e-15) * max(pts[-2][1], 1e-15))
    return hi / lo


def _trend_corr(values: list[float]) -> float:
    """Pearson correlation of residuals against their (sorted-by-n) index:
    +1 = monotone upward drift, 0 = no trend."""
    m = len(values)
    mx = (m - 1) / 2
    my = sum(values) / m
    cov = sum((i - mx) * (v - my) for i, v in enumerate(values))
    vx = sum((i - mx) ** 2 for i in range(m))
    vy = sum((v - my) ** 2 for v in values)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else 0.0


def _scale_step(pts: list[tuple[float, float]], fn,
                overhead: float) -> tuple[float, float] | None:
    """One coefficient step splitting two flat runs: around an allocator
    or threshold event, the per-point scales (y-a)/f(n) of the TRUE class
    are piecewise-constant, while a genuinely higher class drifts smoothly
    (log2 n cannot hold two flat segments over a doubling ladder). Only
    points whose signal dwarfs the overhead floor participate — the floor
    correction bends small-n scales into a fake drift. Returns
    (step_n, ratio) or None."""
    sig = [(n, (y - overhead) / fn(n)) for n, y in pts
           if y > 8 * overhead and fn(n) > 0]
    if len(sig) < MIN_POINTS:
        return None
    for k in range(2, len(sig) - 2):        # >= 2 lo points, >= 3 tail points
        lo = [s for _, s in sig[:k]]
        hi = [s for _, s in sig[k:]]
        if max(lo) / min(lo) <= FLAT_SPREAD \
                and max(hi) / min(hi) <= FLAT_SPREAD:
            ratio = (math.exp(sum(math.log(s) for s in hi) / len(hi))
                     / math.exp(sum(math.log(s) for s in lo) / len(lo)))
            if ratio >= STEP_MIN:
                return sig[k][0], ratio
    return None


def monotonicity_violations(values: list[float]) -> int:
    return sum(1 for a, b in zip(values, values[1:]) if b < 0.8 * a)


def per_element_verdict(pts: list[tuple[float, float]]) -> str | None:
    """Sharper discriminator for the {n, n log n} pair on LOW-NOISE data
    (instruction counts): per-element cost (y-a)/n is flat for O(n) and
    grows ~log n for O(n log n) — a direct test of the log factor that
    class-RMSE blurs when the overhead floor eats half the ladder.
    Returns "flat", "growing", or None (not enough clean signal).
    Thresholds calibrated against evals/harness.py."""
    if len(pts) < MIN_POINTS:
        return None
    pts = sorted(pts)
    a = 0.95 * min(v for _, v in pts)
    signal = [(n, (v - a) / n) for n, v in pts if v > 4 * a and n > 0]
    if len(signal) < 4:
        return None
    span = signal[-1][0] / signal[0][0]
    if span < 8:                       # < 3 doublings of clean signal
        return None
    rise = signal[-1][1] / max(signal[0][1], 1e-15)
    # expected rise for a true log factor over this span
    log_rise = math.log(signal[-1][0]) / math.log(max(signal[0][0], 2.0))
    corr = _trend_corr([r for _, r in signal])
    if rise < min(1.3, 1 + (log_rise - 1) / 2):
        return "flat"
    if rise > 1 + (log_rise - 1) / 2 and corr >= 0.85:
        return "growing"
    return None


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

    # growth must be spread over several points to classify: a flat ladder
    # with one final spike (allocator artifacts, threshold effects) can't
    # support any class claim
    signal_points = sum(1 for _, v in pts if v > 2 * min(v2 for _, v2 in pts))
    if signal_points < 3:
        return FitResult(
            None, [], None,
            f"growth concentrated in {signal_points} point(s) — cannot classify")

    max_n = pts[-1][0]
    scores: list[tuple[float, str]] = []
    scales: dict[str, float] = {}
    for name, fn in CLASSES:
        if name == "O(2^n)" and max_n > EXP_MAX_N:
            continue
        score, b = _fit_class(pts, fn, overhead)
        scores.append((score, name))
        scales[name] = b
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

    # tail cross-check: cache-hierarchy transitions bend the middle of the
    # ladder upward (L1 -> RAM costs ~4x per element in compiled code),
    # which can promote the full-ladder fit one class. The asymptote lives
    # in the tail, where the memory regime has stabilized — if the top
    # half of the ladder fits a lower class, that class is a candidate.
    if len(pts) >= 2 * MIN_POINTS:
        tail = pts[len(pts) // 2:]
        tail_best = min(
            ((_fit_class(tail, fn, overhead)[0], name)
             for name, fn in CLASSES
             if name != "O(2^n)" or max_n <= EXP_MAX_N))[1]
        if CLASS_ORDER[tail_best] < CLASS_ORDER[winner] \
                and tail_best not in candidates:
            candidates.append(tail_best)

    # coefficient-step check: an allocator/threshold event inside the
    # fitted window steps the constant of the TRUE class mid-ladder, which
    # a single-coefficient model can only absorb by promoting the class
    # (n -> n log n soaks up a 2x step across a decade). No fixed re-fit
    # window helps — the step can straddle any of them — so detect the
    # step itself: the highest lower class whose scales form two flat
    # runs split by one jump joins the candidates.
    step_note = ""
    order = sorted(CLASS_ORDER, key=CLASS_ORDER.__getitem__)
    for cand in reversed(order[:order.index(winner)]):
        hit = _scale_step(pts, dict(CLASSES)[cand], overhead)
        if hit:
            if cand not in candidates:
                candidates.append(cand)
            step_note = (f"{STEP_NOTE} ~{hit[1]:.1f}x at n~{int(hit[0])}"
                         f" fits {cand}")
            break

    # residual-trend check: the winner's residuals climbing steadily with
    # n mean the model underfits the top of the ladder — a hidden slow
    # factor (n vs n log n hides exactly here; FSE'07 reads this off the
    # residual plot). The next class up joins the candidates. Downward
    # drift is the tail cross-check's job, so only upward widens here.
    # Only a CLEAN-looking fit (singleton candidate set) is widened: when
    # rivals are already reported the drift is already visible, and
    # stacking a third class turns short-ladder noise into flaky width-3
    # sets (measured at ~4% of 6-7 point ladders).
    fn_w = dict(CLASSES)[winner]
    b_w = scales[winner]
    res = [math.log10(max(y, 1e-15) / max(overhead + b_w * fn_w(n), 1e-15))
           for n, y in pts]
    if (len(candidates) == 1
            and (max(res) - min(res)) > 2 * TIE_BAND
            and _trend_corr(res) >= 0.85):
        order = sorted(CLASS_ORDER, key=CLASS_ORDER.__getitem__)
        for cand in order[order.index(winner) + 1:]:
            if cand == "O(2^n)" and max_n > EXP_MAX_N:
                continue
            if cand not in candidates:
                candidates.append(cand)
            break

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
    reason = "; ".join(r for r in
                       ("close fits" if len(candidates) > 1 else "",
                        step_note) if r)
    if len(candidates) > 1:
        return FitResult(winner, candidates, margin, reason)
    return FitResult(winner, [winner], margin, reason)
