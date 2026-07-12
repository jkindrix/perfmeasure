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
    AMBIGUOUS, CLASS_ORDER, CLASSES, ERROR, MEASURED, TIMEOUT, UNDRIVABLE,
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
    for iv, opt_some in planner.variants(desc):
        drive, reason = planner.plan(desc, fixed_variant=iv,
                                     opt_some=opt_some)
        if drive is None:
            report.provenance = UNDRIVABLE
            report.provenance_detail = reason
            return report
        run = _run_ladders(session, desc, drive, budget, deadline, hard_wall)
        if run.rejected is None or not drive.has_variants:
            break

    report.driver_params = drive.driver_params
    report.fixed_params = drive.fixed_params
    if desc.receiver and drive.receiver_scaled:
        # the receiver is a driver: filled with n items per size (fresh
        # per rep when the method mutates it)
        report.flags["receiver_scaled"] = desc.receiver_fill
    elif desc.receiver:
        report.fixed_params["self"] = desc.receiver + (
            " (fresh per rep)" if desc.receiver_mode == "fresh" else "")
    if (desc.receiver and not drive.receiver_scaled) \
            or any(p.spec_type == "instance_" for p in desc.params):
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
    _aggregate(report, run, probed, budget)
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
    int_only = (not drive.receiver_scaled
                and all(p.spec_type in ("int_mag", "float_mag")
                        for p in desc.params
                        if p.name in drive.driver_params))
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
            has_instr = session.hello.get(
                "capabilities", {}).get("instructions")
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
                if has_instr:
                    # the scale-free channel: 3 extra calls per size buys
                    # the n-vs-n·log·n separation wall time cannot make
                    measure.append("instructions")
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
                ret_deepsize=resp.get("ret_deepsize"),
                instructions=resp.get("instructions")))
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
        if (any(p.instructions is not None for p in s.points)
                and not report.flags.get("suspected_memoization")):
            # scale-free channel; a memoizer's counts measure the cached
            # path, so it gets no ops fit rather than a wrong one
            s.ops_fit = fitting.fit(s.points,
                                    value=lambda p: p.instructions,
                                    floor=fitting.OPS_FLOOR)
            # the {n, n log n} pair gets the sharper per-element test —
            # the log factor is directly visible in (ops - a)/n where
            # class-RMSE is blurred by the call-overhead floor
            if (s.ops_fit.cls
                    and set(s.ops_fit.candidates) == {"O(n)", "O(n log n)"}):
                verdict = fitting.per_element_verdict(
                    [(float(p.n), float(p.instructions)) for p in s.points
                     if p.instructions is not None])
                if verdict == "flat":
                    s.ops_fit.cls = "O(n)"
                    s.ops_fit.candidates = ["O(n)"]
                    s.ops_fit.reason = "per-element flat"
                elif verdict == "growing":
                    s.ops_fit.cls = "O(n log n)"
                    s.ops_fit.candidates = ["O(n log n)"]
                    s.ops_fit.reason = "per-element log growth"
        if any(f and fitting.STEP_NOTE in f.reason
               for f in (s.time_fit, s.space_fit, s.ops_fit)):
            report.flags["coefficient_step_suspected"] = True


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


def _worst(fits: list, report: FunctionReport, value) -> tuple:
    """Worst (shape, fit): highest class wins; same-class ties break on
    measured cost at the largest size every tied shape reached — shape
    iteration order is not evidence, the coefficient is (insertion sort
    is worst on reversed input even though random fits the same class)."""
    top = max(CLASS_ORDER[f.cls] for _, f in fits)
    tied = [(shape, f) for shape, f in fits if CLASS_ORDER[f.cls] == top]
    if len(tied) > 1:
        names = {shape for shape, _ in tied}
        costs = {s.shape: {p.n: value(p) for p in s.points
                           if value(p) is not None}
                 for s in report.per_shape if s.shape in names}
        common = set.intersection(*(set(c) for c in costs.values()))
        if common:
            n = max(common)
            return max(tied, key=lambda t: costs[t[0]][n])
    return tied[0]


def _budget_stopped(stop_reason: str) -> bool:
    return any(tok in stop_reason
               for tok in ("budget", "deadline", "projected_cost"))


def _aggregate(report: FunctionReport, run: _Run, probed: bool,
               budget: Budget) -> None:
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

    shape, fit = _worst(time_fits, report, lambda p: p.seconds)
    report.time_cls = fit.cls
    report.time_candidates = fit.candidates
    report.time_worst_shape = shape
    report.provenance = AMBIGUOUS if len(fit.candidates) > 1 else MEASURED

    ops_fits = [(s.shape, s.ops_fit) for s in report.per_shape
                if s.ops_fit and s.ops_fit.cls]
    if ops_fits:
        _, ofit = max(ops_fits, key=lambda t: CLASS_ORDER[t[1].cls])
        report.ops_cls = ofit.cls
        report.ops_candidates = ofit.candidates
        # headline refinement: a CLEAN instruction fit one class BELOW the
        # wall headline is the algorithmic answer — the higher wall
        # reading is cache physics (dict_get_all reads n log n in wall,
        # dead-flat n in instructions). The wall class stays in the
        # candidate set and is named in a flag, never silently dropped.
        # A gap of more than one class means something else is wrong and
        # nothing is refined.
        adjacent = CLASS_ORDER[fit.cls] - CLASS_ORDER[ofit.cls] == 1
        if (len(ofit.candidates) == 1
                and CLASS_ORDER[ofit.cls] < CLASS_ORDER[fit.cls]
                and (ofit.cls in fit.candidates or adjacent)):
            report.flags["wall_cache_inflated"] = fit.cls
            report.time_cls = ofit.cls
            report.time_candidates = sorted(
                set(fit.candidates) | {ofit.cls},
                key=CLASS_ORDER.__getitem__, reverse=True)
            report.provenance = (AMBIGUOUS if len(report.time_candidates) > 1
                                 else MEASURED)

    if space_fits:
        sshape, sfit = _worst(space_fits, report, lambda p: p.peak_bytes)
        report.space_cls = sfit.cls
        report.space_candidates = sfit.candidates
        report.space_worst_shape = sshape

    # a time-O(1) verdict is an absence-of-growth claim, and budget
    # truncation weakens exactly that evidence: when every fitted ladder
    # was stopped by the budget (not by n_max or by the data), a large
    # constant may still be masking a term that never got room to emerge.
    # A true cheap O(1) reaches n_max, so it never carries this flag.
    # (Space stays out: O(1) space with a budget-stopped ladder describes
    # half of all real functions — the flag would be noise, and the
    # deep-size blindspot check already covers masked space growth.)
    if report.time_cls == "O(1)":
        fitted = [s for s in report.per_shape if s.time_fit and s.time_fit.cls]
        if fitted and all(_budget_stopped(s.stop_reason) for s in fitted):
            report.flags["constant_within_budget_window"] = report.max_n_reached

    # a hard timeout ABOVE the fitted window that the fitted class cannot
    # explain (extrapolating the winner's own curve to the timeout size
    # predicts a fraction of the timeout) is evidence of a steeper regime
    # past the window — surface it instead of leaving it buried in
    # per-shape failures. The killed CALL is warmup + reps + a traced
    # pass (several executions), so a class projecting anywhere near
    # timeout/execs honestly explains the kill and stays quiet; only a
    # projection far below that (a 20x+ single-call overrun) defies it.
    tmo_ns = [f["n"] for s in report.per_shape for f in s.failures
              if f["kind"] == "timeout_hard"]
    if tmo_ns and report.time_cls and report.max_n_reached:
        worst_pts = next(
            (s.points for s in report.per_shape if s.shape == shape), [])
        if worst_pts:
            last = max(worst_pts, key=lambda p: p.n)
            fn_cls = dict(CLASSES)[report.time_cls]
            n_t = max(tmo_ns)
            if n_t > last.n and fn_cls(last.n) > 0:
                projected = last.seconds * fn_cls(n_t) / fn_cls(last.n)
                if projected < 0.05 * budget.hard_timeout_s:
                    report.flags["timeout_above_window"] = n_t

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
    # model-mismatch suspicions demote like every other suspicion flag:
    # a detected coefficient step or an allocator blindspot means the
    # reported class rests on a model the data visibly bent away from
    if report.flags.get("coefficient_step_suspected"):
        conf = lower_confidence(conf)
    if report.flags.get("untracked_alloc_suspected"):
        conf = lower_confidence(conf)
    if report.flags.get("constant_within_budget_window"):
        conf = lower_confidence(conf)
    if report.flags.get("timeout_above_window"):
        conf = lower_confidence(conf)
    if probed and conf == "high":
        conf = "med"        # probed types cap confidence at medium
    if report.flags.get("fixed_instance_inputs") and conf == "high":
        conf = "med"
    report.confidence = conf
