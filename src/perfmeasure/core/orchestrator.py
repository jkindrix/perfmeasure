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
SPAN_CONFIDENT_DOUBLINGS = 5
ADJACENT_SPAN_DOUBLINGS = 6   # n vs n log n needs extra span for `high`
DEEPSIZE_GROWTH = 8.0


@dataclass
class _Run:
    shapes: list[ShapeResult] = field(default_factory=list)
    spread_flags: int = 0
    mutates: bool = False
    first_probe_timeout: bool = False
    shapes_skipped: int = 0          # deadline hit before these shapes ran
    recv_mutates: bool = False       # method mutated self; fresh bind per rep
    rejected: str | None = None      # exception message at the first size
    retained_state: bool = False     # warmup call retained heap (Rust
                                     # interior-mutability cache signal)


def measure_function(session: RunnerSession, desc: FunctionDescriptor,
                     budget: Budget) -> FunctionReport:
    report = FunctionReport(
        fid=desc.fid, file=desc.file, line=desc.line, provenance=MEASURED,
        environment={"runtime": session.hello.get("runtime", "?")},
    )
    started = time.perf_counter()
    deadline = started + budget.per_function_s
    hard_wall = deadline + budget.rescue_s

    probed, probe_fail = probing.probe(session, desc, deadline=deadline)
    if probe_fail:
        if probe_fail.startswith("deadline:"):
            # the budget died mid-probe; we learned nothing about the
            # function itself, so UNDRIVABLE would be a lie
            report.provenance = TIMEOUT
        else:
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
        run = _run_ladders(session, desc, drive, budget, deadline, hard_wall)
        if run.rejected is None or not drive.has_fixed_ints:
            break

    report.driver_params = drive.driver_params
    report.fixed_params = drive.fixed_params
    if desc.receiver:
        report.fixed_params["self"] = desc.receiver + (
            " (fresh per rep)" if desc.receiver_mode == "fresh" else "")
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
    if session.hello.get("platform"):
        # the platform where measurement RAN (the runner's), which is not
        # necessarily the orchestrator's for remote/containerized runners
        report.environment["platform"] = session.hello["platform"]
    if session.hello.get("opt_profile"):
        # the opt profile the harness was really built with (mirrored
        # from the target's workspace, divergences named) — measured
        # numbers are only comparable to builds with the same profile
        report.environment["opt_profile"] = session.hello["opt_profile"]
    report.allocator = session.hello.get(
        "capabilities", {}).get("memory") or "unknown"
    if run.mutates:
        report.flags["mutates_input"] = True
    if run.recv_mutates or desc.receiver_mode == "fresh":
        report.flags["mutates_receiver"] = True
    if run.retained_state:
        # the first call left heap behind (interner, memo table, lazy
        # static): later reps may have measured a cached path
        report.flags["state_retained_after_first_call"] = True

    if run.rejected is not None:
        report.provenance = UNDRIVABLE
        report.provenance_detail = f"rejected: {run.rejected}"
        return report

    _fit_shapes(run, report)
    _aggregate(report, run, probed)
    # black_box is best-effort by documented contract: fully optimized-out
    # compiled code reads as a flat sub-nanosecond O(1) — detectable, so
    # detect it instead of reporting the deletion as a measurement
    if (report.time_cls == "O(1)"
            and session.hello.get("language") == "rust"):
        secs = [p.seconds for s in report.per_shape for p in s.points]
        if secs and min(secs) < 1e-9:
            report.flags["possible_optimizer_elision"] = True
    return report


def _run_ladders(session: RunnerSession, desc: FunctionDescriptor,
                 drive: DrivePlan, budget: Budget, deadline: float,
                 hard_wall: float) -> _Run:
    run = _Run()
    int_only = all(p.spec_type == "int_mag" for p in desc.params
                   if p.name in drive.driver_params)
    n0 = N0_INT if int_only else N0_COLLECTION
    n_max = INT_N_MAX if int_only else N_MAX
    for i, shape in enumerate(drive.shapes):
        remaining = deadline - time.perf_counter()
        if remaining <= 0.05:
            # deadline: --budget is a promise, not a suggestion; skipped
            # shapes are counted, never silently absorbed by a floor
            run.shapes_skipped = len(drive.shapes) - i
            break
        shape_budget = remaining / (len(drive.shapes) - i)
        ladder = ShapeLadder(n0=n0, budget_s=shape_budget, n_max=n_max,
                             per_call_soft_s=budget.per_call_soft_s)
        result = ShapeResult(shape=shape, points=[])
        while (n := ladder.next_size()) is not None:
            now = time.perf_counter()
            if now >= hard_wall or (now >= deadline
                                    and not ladder._backfilling):
                result.stop_reason = result.stop_reason or "deadline"
                break
            specs = [s.wire() for s in drive.specs(shape, n)]
            # memory needs >= MIN_POINTS observations even on a minimal
            # five-point ladder, so the first five SUCCESSFUL points are
            # always traced (failed attempts don't consume slots); after
            # that, alternating halves the tracing overhead at the
            # expensive top with no fit-quality loss
            npts = len(result.points)
            # first five successful points always trace memory (the
            # fitter's minimum), alternating after. Rescue calls measure
            # TIME ONLY and run lean: the rescue window affords exactly
            # one timed call on a function steep enough to have needed
            # rescuing — its space, if unaffordable, stays honestly
            # unmeasured rather than costing the time fit
            if ladder._backfilling:
                measure = ["time"]
                # no warmup means no runner-side mutation detection: pass
                # down what this run already learned, or rep 2 of a
                # mutating function would measure a dirtied input
                lean = {"warmup": 0, "max_repeats": 2, "min_total_ms": 0,
                        "known_mutates": run.mutates,
                        "known_recv_mutates": run.recv_mutates}
            else:
                measure = (["time", "memory"] if npts < 5 or npts % 2 == 1
                           else ["time"])
                lean = {"warmup": budget.warmup,
                        "max_repeats": budget.max_repeats,
                        "min_total_ms": budget.min_total_ms}
            # budget is a promise; rescue_s is the only sanctioned overrun.
            # NORMAL calls may wait only until the deadline — the rescue
            # window is reserved for backfill, so killing a hang can never
            # consume the very window that salvages the fit afterwards
            wall = hard_wall if ladder._backfilling else deadline
            req_timeout = min(budget.hard_timeout_s,
                              max(0.25, wall - time.perf_counter()))
            # the runner's internal rep budget stays inside the request
            # window (0.8: headroom to serialize + flush) so a deadline-
            # squeezed call returns partial reps instead of dying mid-rep
            msg = protocol.call_msg(
                session.next_id(), desc.fid, specs,
                measure=measure, **lean,
                budget_ms=int(min(shape_budget, budget.hard_timeout_s,
                                  req_timeout * 0.8) * 1000))
            t0 = time.perf_counter()
            resp = session.request(msg, timeout=req_timeout)
            ladder.charge(time.perf_counter() - t0)
            if resp["op"] == "error":
                kind = resp["kind"]
                if kind == "timeout_hard" and req_timeout < budget.hard_timeout_s:
                    # the deadline, not the function, killed this call: a
                    # scheduling fact, never a steepness or defect signal
                    kind = "deadline_exhausted"
                result.failures.append(
                    {"n": n, "kind": kind, "message": resp["message"]})
                # deadline_exhausted takes the generic force-backfill path
                # below: the rescue window exists precisely to salvage a
                # steep ladder after a deadline kill. It must only never
                # read as first_probe_timeout (that blames the function).
                if kind == "exception" and not result.points and i == 0:
                    run.rejected = resp["message"].splitlines()[-1][:200]
                    run.shapes.append(result)
                    return run
                if kind in ("timeout_hard", "runner_crash") \
                        and not result.points and i == 0:
                    run.first_probe_timeout = True
                    result.stop_reason = kind
                    break
                reason = (kind if kind != "exception"
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
            if resp.get("mutates_receiver"):
                run.recv_mutates = True
            if any(str(note).startswith("retained_bytes:")
                   for note in resp.get("notes", [])):
                run.retained_state = True
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
        elif any(f["kind"] == "deadline_exhausted" for s in report.per_shape
                 for f in s.failures):
            report.provenance = TIMEOUT
            report.provenance_detail = (
                "per-function budget exhausted before a usable measurement "
                "(steep, hung, or under-budgeted — indistinguishable here)")
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
    span = math.log2(max(ns) / min(ns)) if ns else 0.0
    if ns and span < SPAN_CONFIDENT_DOUBLINGS:
        conf = lower_confidence(conf)
    # distinguishing n from n log n over a short ladder rests on a log
    # factor moving ~2-3x total; `high` for that pair needs extra span
    if (conf == "high" and span < ADJACENT_SPAN_DOUBLINGS
            and {"O(n)", "O(n log n)"} <= set(fit.candidates)):
        conf = "med"
    if fitting.monotonicity_violations([p.seconds for p in worst.points]):
        conf = lower_confidence(conf)
        report.flags["monotonicity_violations"] = True
    if run.spread_flags:
        conf = lower_confidence(conf)
        report.flags["rep_spread_over_5x"] = run.spread_flags
    if report.flags.get("suspected_memoization"):
        conf = lower_confidence(conf)
    if report.flags.get("state_retained_after_first_call"):
        conf = lower_confidence(conf)
    if probed and conf == "high":
        conf = "med"        # probed types cap confidence at medium
    if report.flags.get("fixed_instance_inputs") and conf == "high":
        conf = "med"
    report.confidence = conf
