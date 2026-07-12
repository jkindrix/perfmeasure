"""Size ladder and budget control.

Doubling schedule with adaptive stopping. Stop conditions per shape:
  - per-call soft cap exceeded by the last measured call
  - projected-cost gate: never START a call whose 4x extrapolation
    (quadratic-growth assumption) would bust the remaining shape budget
  - size ceiling reached
  - shape wall budget exhausted

If a shape stops steeply (soft cap) with too few points for a trustworthy
fit, midpoints between already-measured sizes are backfilled largest-first
— this is what gives exponential/cubic functions enough points in the
regime where they are measurable at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

N0_COLLECTION = 4
N0_INT = 2
N_MAX = 2 ** 20          # collections: past ~1M elements, materialization
                         # and memory passes dominate wall time for no fit gain
INT_N_MAX = 2 ** 62      # int magnitude costs no memory; log-class
                         # functions need the span (digits, halvings)
MIN_POINTS_TARGET = 5


@dataclass
class Budget:
    """per_function_s is a monotonic deadline shared by probing, ladders,
    requests, and shape scheduling. rescue_s is the ONLY sanctioned
    overrun: a bounded window past the deadline in which a hang can be
    killed and a steep ladder backfilled. Wall time never exceeds
    per_function_s + rescue_s (plus process overhead)."""
    per_function_s: float = 30.0
    per_call_soft_s: float = 1.0
    hard_timeout_s: float = 10.0
    rescue_s: float = 4.0


@dataclass
class ShapeLadder:
    n0: int
    budget_s: float
    per_call_soft_s: float
    n_max: int = N_MAX
    sizes_done: list[int] = field(default_factory=list)
    times: dict[int, float] = field(default_factory=dict)
    spent_s: float = 0.0
    stop_reason: str = ""
    _backfilling: bool = False
    _failed_n: int | None = None

    def record(self, n: int, seconds: float, wall_cost: float) -> None:
        self.sizes_done.append(n)
        self.times[n] = seconds
        self.spent_s += wall_cost

    def charge(self, wall_cost: float) -> None:
        self.spent_s += wall_cost

    def force_backfill(self, failed_n: int, reason: str,
                       grace_s: float | None = None) -> bool:
        """A size failed (timeout/exception): switch to backfilling midpoints
        so steep functions still reach a fittable point count. Returns False
        if already backfilling (second failure -> caller should stop)."""
        if self._backfilling:
            return False
        self._failed_n = failed_n
        self._backfilling = True
        self.stop_reason = reason
        # the failure (e.g. a hard timeout) may have consumed the whole
        # shape budget; grant a bounded grace so the midpoints that rescue
        # the fit still run even under machine load (an eval flaked here)
        if grace_s is None:
            grace_s = 4.0 * self.per_call_soft_s
        self.budget_s = max(self.budget_s, self.spent_s + grace_s)
        return True

    def next_size(self) -> int | None:
        if self.spent_s >= self.budget_s:
            self.stop_reason = self.stop_reason or "budget"
            return None
        if not self.sizes_done:
            return self.n0
        last = self.sizes_done[-1]
        last_t = self.times.get(last, 0.0)
        if not self._backfilling:
            if last_t > self.per_call_soft_s:
                self.stop_reason = "per_call_cap"
            elif last * 2 > self.n_max:
                self.stop_reason = "n_max"
            elif last_t * 4 > self.budget_s - self.spent_s:
                self.stop_reason = "projected_cost"
            else:
                return last * 2
            self._backfilling = True
        return self._next_backfill()

    def _next_backfill(self) -> int | None:
        """Midpoints between measured sizes — plus one probe into the gap
        toward a failed size — largest gap first, until the fitter has
        enough points. Only reached after a steep stop."""
        if len(self.sizes_done) >= MIN_POINTS_TARGET:
            return None
        done = sorted(set(self.sizes_done))
        edges = list(zip(done, done[1:]))
        if self._failed_n is not None and done:
            edges.append((done[-1], self._failed_n))
        gaps = sorted(((b - a, a, b) for a, b in edges if b - a >= 2),
                      reverse=True)
        for _, a, b in gaps:
            mid = (a + b) // 2
            if mid not in self.times:
                # a measured upper edge bounds the midpoint's cost; a failed
                # upper edge doesn't, but the hard timeout bounds the risk
                if self.times.get(b, 0.0) > self.budget_s - self.spent_s:
                    self.stop_reason += "+backfill_budget"
                    return None
                return mid
        return None
