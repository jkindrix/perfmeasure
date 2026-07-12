"""Per-function measurement loop: probe -> plan -> ladder per shape -> fit.

Failure taxonomy (settled): an exception at the very first size means the
generated input is rejected — the orchestrator walks the fixed-int
fallback variants (1, 0, half-of-driver) before giving up as
UNDRIVABLE(rejected). Exceptions that only appear at larger sizes
(RecursionError, MemoryError) keep the smaller points and backfill
midpoints. A hard timeout kills the runner and ends the shape; if even
the first probe size times out the function is TIMEOUT. Crashes are data.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from perfmeasure import protocol
from perfmeasure.core import fitting, planner, probing
from perfmeasure.core.ladder import (
    INT_N_MAX, N_MAX, N0_COLLECTION, N0_INT, Budget, ShapeLadder,
)
from perfmeasure.core.model import (
    AMBIGUOUS, CLASS_ORDER, ERROR, MEASURED, TIMEOUT, UNDRIVABLE,
    DrivePlan, FunctionDescriptor, FunctionReport, Point, ShapeResult,
    lower_confidence,
)
from perfmeasure.session import RunnerSession

REP_SPREAD_LIMIT = 5.0
MEMO_RATIO = 10.0
MEMO_ABS_FLOOR_S = 50e-6
SPAN_CONFIDENT_DOUBLINGS = 4
DEEPSIZE_GROWTH = 8.0


@dataclass
class _Run:
    shapes: list[ShapeResult] = field(default_factory=list)
    spread_flags: int = 0
    mutates: bool = False
    first_probe_timeout: bool = False
    shapes_skipped: int = 0          # deadline hit before these shapes ran
    rejected: str | None = None      # exception message at the first size


def measure_function(session: RunnerSession, desc: FunctionDescriptor,
                     budget: Budget) -> FunctionReport:
    report = FunctionReport(
        fid=desc.fid, file=desc.file, line=desc.line, provenance=MEASURED,
        environment={"runtime": session.hello.get("runtime", "?")},
    )
    started = time.perf_counter()

    probed, probe_fail = probing.probe(
        session, desc, deadline_left=budget.per_function_s)
    if probe_fail:
        report.provenance = UNDRIVABLE
        report.provenance_detail = probe_fail
        return report

    run = None
    for variant in range(planner.FIXED_VARIANTS):
        drive, reason = planner.plan(desc, fixed_variant=variant)
        if drive is None:
            report.provenance = UNDRIVABLE
            report.provenance_detail = reason
            return report
        run = _run_ladders(session, desc, drive, budget)
        if run.rejected is None or not drive.fixed_params:
            break

    report.driver_params = drive.driver_params
    report.fixed_params = drive.fixed_params
    if desc.receiver:
        report.fixed_params["self"] = desc.receiver
    if desc.receiver or any(p.spec_type == "instance_" for p in desc.params):
        # measured against default-constructed instances: cost that depends
        # on instance state is invisible at this fixed point
        report.flags["fixed_instance_inputs"] = True
    report.type_source = {
        p.name: ("probed" if p.detail == "probed" else "hinted")
        for p in desc.params if p.name in drive.driver_params}
    report.per_shape = run.shapes
    report.max_n_reached = max(
        (p.n for s in run.shapes for p in s.points), default=0)
    report.wall_used_s = time.perf_counter() - started
    if run.shapes_skipped:
        report.flags["deadline_shapes_skipped"] = run.shapes_skipped
    # hello is only populated after the first request; refresh now
    report.environment = {"runtime": session.hello.get("runtime", "?")}
    report.allocator = session.hello.get(
        "capabilities", {}).get("memory") or "unknown"
    if run.mutates:
        report.flags["mutates_input"] = True

    if run.rejected is not None:
        report.provenance = UNDRIVABLE
        report.provenance_detail = f"rejected: {run.rejected}"
        return report

    _fit_shapes(run, report)
    _aggregate(report, run, probed)
    return report


def _run_ladders(session: RunnerSession, desc: FunctionDescriptor,
                 drive: DrivePlan, budget: Budget) -> _Run:
    run = _Run()
    int_only = all(p.spec_type == "int_mag" for p in desc.params
                   if p.name in drive.driver_params)
    n0 = N0_INT if int_only else N0_COLLECTION
    n_max = INT_N_MAX if int_only else N_MAX
    started = time.perf_counter()
    for i, shape in enumerate(drive.shapes):
        remaining = budget.per_function_s - (time.perf_counter() - started)
        if remaining <= 0.05:
            # deadline: --budget is a promise, not a suggestion; skipped
            # shapes are counted, never silently absorbed by a floor
            run.shapes_skipped = len(drive.shapes) - i
            break
        shape_budget = remaining / (len(drive.shapes) - i)
        ladder = ShapeLadder(n0=n0, budget_s=shape_budget, n_max=n_max,
                             per_call_soft_s=budget.per_call_soft_s)
        result = ShapeResult(shape=shape, points=[])
        size_idx = 0
        while (n := ladder.next_size()) is not None:
            specs = [s.wire() for s in drive.specs(shape, n)]
            # memory is near-deterministic: tracing every other size halves
            # the tracing overhead at the expensive top of the ladder while
            # keeping >= MIN_POINTS memory points on any fittable ladder
            measure = ["time", "memory"] if size_idx % 2 == 0 else ["time"]
            size_idx += 1
            deadline_left = budget.per_function_s - (
                time.perf_counter() - started)
            msg = protocol.call_msg(
                session.next_id(), desc.fid, specs,
                measure=measure,
                budget_ms=int(min(shape_budget, budget.hard_timeout_s) * 1000))
            t0 = time.perf_counter()
            # one monotonic deadline governs requests too: never wait longer
            # than the remaining budget plus one bounded rescue window
            resp = session.request(msg, timeout=min(
                budget.hard_timeout_s,
                max(1.0, deadline_left + 2 * budget.per_call_soft_s)))
            ladder.charge(time.perf_counter() - t0)
            if resp["op"] == "error":
                result.failures.append(
                    {"n": n, "kind": resp["kind"], "message": resp["message"]})
                if resp["kind"] == "exception" and not result.points and i == 0:
                    run.rejected = resp["message"].splitlines()[-1][:200]
                    run.shapes.append(result)
                    return run
                if resp["kind"] in ("timeout_hard", "runner_crash") \
                        and not result.points and i == 0:
                    run.first_probe_timeout = True
                    result.stop_reason = resp["kind"]
                    break
                reason = (resp["kind"] if resp["kind"] != "exception"
                          else "exception_at_larger_n")
                if ladder.force_backfill(n, reason):
                    continue
                result.stop_reason = reason
                break
            timings = resp["wall_seconds"]
            if timings and max(timings) > REP_SPREAD_LIMIT * min(timings):
                run.spread_flags += 1
            if resp.get("mutates"):
                run.mutates = True
            result.points.append(Point(
                n=n, seconds=min(timings) if timings else 0.0,
                reps=resp["repeats_done"],
                peak_bytes=resp.get("peak_alloc_bytes"),
                batched=resp.get("batched", False),
                first_seconds=timings[0] if timings else 0.0,
                warmup_seconds=resp.get("warmup_seconds"),
                ret_deepsize=resp.get("ret_deepsize")))
            ladder.record(n, result.points[-1].seconds, 0.0)
        result.stop_reason = result.stop_reason or ladder.stop_reason
        run.shapes.append(result)
    return run


def _memoized(points: list[Point]) -> bool:
    """The first-ever call (warmup) dwarfing every timed rep, repeatedly,
    means later calls hit a cache — the timed reps measure the wrong thing.
    The absolute floor matters: a single-sample warmup of a nanosecond-scale
    function reads hundreds of ns of timer noise, which would fake the
    ratio against batch-averaged reps."""
    hits = sum(1 for p in points
               if p.warmup_seconds is not None and p.first_seconds > 0
               and p.warmup_seconds > max(MEMO_RATIO * p.first_seconds,
                                          MEMO_ABS_FLOOR_S))
    return hits >= 2


def _fit_shapes(run: _Run, report: FunctionReport) -> None:
    for s in run.shapes:
        if not s.points:
            continue
        if _memoized(s.points):
            report.flags["suspected_memoization"] = True
            s.time_fit = fitting.fit(
                [p for p in s.points if p.warmup_seconds is not None],
                value=lambda p: p.warmup_seconds)
        else:
            s.time_fit = fitting.fit(s.points)
        if any(p.peak_bytes is not None for p in s.points):
            s.space_fit = fitting.fit(s.points, value=lambda p: p.peak_bytes,
                                      floor=fitting.MEM_FLOOR)
            _blindspot_check(s, report)


def _blindspot_check(s: ShapeResult, report: FunctionReport) -> None:
    """tracemalloc can't see C-extension allocations: if the return value's
    deep-size grows while traced peak stays flat, the space answer is
    demoted to an honest ambiguity instead of a confident O(1)."""
    if s.space_fit is None or s.space_fit.cls != "O(1)":
        return
    sizes = [(p.n, p.ret_deepsize) for p in s.points
             if p.ret_deepsize is not None]
    if len(sizes) < fitting.MIN_POINTS:
        return
    sizes.sort()
    if sizes[-1][1] >= DEEPSIZE_GROWTH * max(sizes[0][1], 1):
        deep_fit = fitting.fit(
            [Point(n=n, seconds=0, reps=1, peak_bytes=v) for n, v in sizes],
            value=lambda p: p.peak_bytes, floor=64.0)
        grown = deep_fit.cls or "O(n)"
        s.space_fit.candidates = sorted(
            set(s.space_fit.candidates) | {grown, "O(1)"},
            key=CLASS_ORDER.__getitem__, reverse=True)
        s.space_fit.cls = grown
        s.space_fit.reason = "allocations invisible to tracemalloc"
        report.flags["untracked_alloc_suspected"] = True


def _aggregate(report: FunctionReport, run: _Run, probed: bool) -> None:
    time_fits = [(s.shape, s.time_fit) for s in report.per_shape
                 if s.time_fit and s.time_fit.cls]
    space_fits = [(s.shape, s.space_fit) for s in report.per_shape
                  if s.space_fit and s.space_fit.cls]

    if not time_fits:
        if run.first_probe_timeout:
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

    shape, fit = max(time_fits, key=lambda t: CLASS_ORDER[t[1].cls])
    report.time_cls = fit.cls
    report.time_candidates = fit.candidates
    report.time_worst_shape = shape
    report.provenance = AMBIGUOUS if len(fit.candidates) > 1 else MEASURED

    if space_fits:
        sshape, sfit = max(space_fits, key=lambda t: CLASS_ORDER[t[1].cls])
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
    if run.spread_flags:
        conf = lower_confidence(conf)
        report.flags["rep_spread_over_5x"] = run.spread_flags
    if report.flags.get("suspected_memoization"):
        conf = lower_confidence(conf)
    if probed and conf == "high":
        conf = "med"        # probed types cap confidence at medium
    if report.flags.get("fixed_instance_inputs") and conf == "high":
        conf = "med"
    report.confidence = conf
