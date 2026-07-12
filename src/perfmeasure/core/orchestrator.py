"""Per-function measurement loop: plan -> ladder per shape -> fit -> report.

Failure taxonomy (settled): an exception at the very first size means the
generated input is rejected -> UNDRIVABLE(rejected). Exceptions that only
appear at larger sizes (RecursionError, MemoryError) keep the smaller
points. A hard timeout kills the runner and ends the shape; if even the
first probe size times out the function is TIMEOUT. Crashes are data.
"""
from __future__ import annotations

import math
import time

from perfmeasure import protocol
from perfmeasure.core import fitting, planner
from perfmeasure.core.ladder import N0_COLLECTION, N0_INT, Budget, ShapeLadder
from perfmeasure.core.model import (
    AMBIGUOUS, ERROR, MEASURED, TIMEOUT, UNDRIVABLE,
    FunctionDescriptor, FunctionReport, Point, ShapeResult,
    lower_confidence, worst_class,
)
from perfmeasure.session import RunnerSession

REP_SPREAD_LIMIT = 5.0
SPAN_CONFIDENT_DOUBLINGS = 4


def measure_function(session: RunnerSession, desc: FunctionDescriptor,
                     budget: Budget) -> FunctionReport:
    report = FunctionReport(
        fid=desc.fid, file=desc.file, line=desc.line, provenance=MEASURED,
        environment={"runtime": session.hello.get("runtime", "?")},
    )
    drive, reason = planner.plan(desc)
    if drive is None:
        report.provenance = UNDRIVABLE
        report.provenance_detail = reason
        return report
    report.driver_params = drive.driver_params
    report.fixed_params = drive.fixed_params
    report.type_source = {p: "hinted" for p in drive.driver_params}

    n0 = N0_INT if all(
        p.spec_type == "int_mag" for p in desc.params
        if p.name in drive.driver_params) else N0_COLLECTION

    started = time.perf_counter()
    spread_flags = 0
    first_probe_timeout = False
    for i, shape in enumerate(drive.shapes):
        remaining = budget.per_function_s - (time.perf_counter() - started)
        shape_budget = max(0.5, remaining / (len(drive.shapes) - i))
        ladder = ShapeLadder(n0=n0, budget_s=shape_budget,
                             per_call_soft_s=budget.per_call_soft_s)
        result = ShapeResult(shape=shape, points=[])
        while (n := ladder.next_size()) is not None:
            specs = [s.wire() for s in drive.specs(shape, n)]
            msg = protocol.call_msg(
                session.next_id(), desc.fid, specs,
                measure=["time", "memory"],
                budget_ms=int(min(shape_budget, budget.hard_timeout_s) * 1000))
            t0 = time.perf_counter()
            resp = session.request(msg, timeout=budget.hard_timeout_s)
            wall = time.perf_counter() - t0
            if resp["op"] == "error":
                result.failures.append(
                    {"n": n, "kind": resp["kind"], "message": resp["message"]})
                if resp["kind"] == "exception" and not result.points and i == 0:
                    report.provenance = UNDRIVABLE
                    report.provenance_detail = f"rejected: {resp['message'].splitlines()[-1][:200]}"
                    report.per_shape.append(result)
                    return report
                ladder.charge(wall)
                if resp["kind"] in ("timeout_hard", "runner_crash"):
                    if not result.points and i == 0:
                        first_probe_timeout = True
                        result.stop_reason = resp["kind"]
                        break
                    # steep stop: backfill midpoints so the fit still has
                    # enough points; a second failure ends the shape
                    if ladder.force_backfill(n, resp["kind"]):
                        continue
                    result.stop_reason = resp["kind"]
                    break
                # exception at larger n: keep smaller points, try midpoints
                if ladder.force_backfill(n, "exception_at_larger_n"):
                    continue
                result.stop_reason = "exception_at_larger_n"
                break
            timings = resp["wall_seconds"]
            if timings and max(timings) > REP_SPREAD_LIMIT * min(timings):
                spread_flags += 1
            point = Point(n=n, seconds=min(timings) if timings else 0.0,
                          reps=resp["repeats_done"],
                          peak_bytes=resp.get("peak_alloc_bytes"),
                          batched=resp.get("batched", False))
            result.points.append(point)
            ladder.record(n, point.seconds, wall)
        result.stop_reason = result.stop_reason or ladder.stop_reason
        if result.points:
            result.time_fit = fitting.fit(result.points)
            if any(p.peak_bytes is not None for p in result.points):
                result.space_fit = fitting.fit(
                    result.points, value=lambda p: p.peak_bytes,
                    floor=fitting.MEM_FLOOR)
        report.per_shape.append(result)
        report.max_n_reached = max(report.max_n_reached,
                                   max((p.n for p in result.points), default=0))

    report.wall_used_s = time.perf_counter() - started
    _aggregate(report, spread_flags, first_probe_timeout)
    return report


def _aggregate(report: FunctionReport, spread_flags: int,
               first_probe_timeout: bool) -> None:
    time_fits = [(s.shape, s.time_fit) for s in report.per_shape
                 if s.time_fit and s.time_fit.cls]
    space_fits = [(s.shape, s.space_fit) for s in report.per_shape
                  if s.space_fit and s.space_fit.cls]

    if not time_fits:
        if first_probe_timeout:
            report.provenance = TIMEOUT
            report.provenance_detail = "first probe size exceeded the hard timeout"
        elif any(f["kind"] == "runner_crash" for s in report.per_shape
                 for f in s.failures):
            report.provenance = ERROR
            report.provenance_detail = "runner crashed"
        else:
            report.provenance = UNDRIVABLE
            reasons = [s.time_fit.reason for s in report.per_shape
                       if s.time_fit and s.time_fit.reason]
            report.provenance_detail = (
                "insufficient_range: " + reasons[0] if reasons
                else "no successful measurements")
        return

    shape, fit = max(time_fits, key=lambda t: _order(t[1].cls))
    report.time_cls = fit.cls
    report.time_candidates = fit.candidates
    report.time_worst_shape = shape
    report.provenance = AMBIGUOUS if len(fit.candidates) > 1 else MEASURED

    if space_fits:
        sshape, sfit = max(space_fits, key=lambda t: _order(t[1].cls))
        report.space_cls = sfit.cls
        report.space_candidates = sfit.candidates
        report.space_worst_shape = sshape

    conf = "high"
    if fit.margin is not None and fit.margin < fitting.CONFIDENT_MARGIN:
        conf = lower_confidence(conf)
    worst = next(s for s in report.per_shape if s.shape == shape)
    ns = [p.n for p in worst.points]
    if ns and math.log2(max(ns) / min(ns)) < SPAN_CONFIDENT_DOUBLINGS:
        conf = lower_confidence(conf)
    if fitting.monotonicity_violations([p.seconds for p in worst.points]):
        conf = lower_confidence(conf)
        report.flags["monotonicity_violations"] = True
    if spread_flags:
        conf = lower_confidence(conf)
        report.flags["rep_spread_over_5x"] = spread_flags
    report.confidence = conf


def _order(cls: str) -> int:
    from perfmeasure.core.model import CLASS_ORDER
    return CLASS_ORDER[cls]
