from __future__ import annotations

from dataclasses import dataclass, field

from perf_lint.costs import CONST_COST, LINEAR, NLOGN, QUADRATIC_GROWTH, CostTable
from perf_lint.ir import CONST, GROWN, Call, Function, Loop, Node, Op

HIGH = "HIGH"
MED = "MED"
UNKNOWN = "UNKNOWN"

_LETTERS = "nmkpqr"

# ordered product: list of (symbol, exponent); logs: symbols under a log factor
Counts = list[tuple[str, int]]


@dataclass
class Finding:
    file: str
    line: int
    function: str
    severity: str  # HIGH | MED | UNKNOWN
    complexity: str  # e.g. "O(n^2)", "O(n*m)", "?"
    message: str
    # lines of the loop chain this finding covers, for prefix dedup
    chain: tuple[int, ...] = ()


@dataclass
class Summary:
    """Worst cost of a function expressed in its positional parameters."""

    params: list[str]
    param_exps: dict[str, int]
    log_params: set[str]
    file: str
    line: int


@dataclass
class _Event:
    etype: str  # "loops" | "op" | "call" | "unknown_loop" | "recursion"
    node: Node
    stack: list[Loop]
    counts: Counts
    logs: list[str]
    displays: dict[str, str]
    extra: dict = field(default_factory=dict)


def analyze_function(
    fn: Function,
    costs: CostTable | None = None,
    summaries: dict[str, Summary] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for ev in _events(fn, costs, summaries):
        if ev.etype == "loops":
            if _degree(ev.counts) >= 2:
                findings.append(_loops_finding(ev, fn))
        elif ev.etype in ("op", "call"):
            if _degree(ev.counts) >= 2:
                findings.append(_cost_finding(ev, fn))
        elif ev.etype == "unknown_loop":
            findings.append(Finding(
                file=fn.file, line=ev.node.line, function=fn.name,
                severity=UNKNOWN, complexity="?",
                message="while-loop bound depends on runtime data — not analyzed",
            ))
        elif ev.etype == "recursion":
            findings.append(Finding(
                file=fn.file, line=ev.node.line, function=fn.name,
                severity=UNKNOWN, complexity="?",
                message=f"recursive call to `{ev.node.callee}` — recursion not analyzed",
            ))
    return _dedup_prefixes(findings)


def build_summaries(
    functions: list[Function], costs: CostTable | None
) -> dict[str, Summary]:
    """One-level cost summaries: each function's own worst cost, in its params."""
    by_name: dict[str, Function | None] = {}
    for fn in functions:
        by_name[fn.name] = None if fn.name in by_name else fn  # None = ambiguous

    summaries: dict[str, Summary] = {}
    for name, fn in by_name.items():
        if fn is None or not fn.params:
            continue
        param_syms = {f"size:{p}": p for p in fn.params}
        best_exps: dict[str, int] = {}
        best_logs: set[str] = set()
        for ev in _events(fn, costs, None):
            if ev.etype not in ("loops", "op"):
                continue
            exps = {param_syms[s]: e for s, e in ev.counts if s in param_syms}
            if sum(exps.values()) > sum(best_exps.values()):
                best_exps = exps
                best_logs = {param_syms[s] for s in ev.logs if s in param_syms}
        if best_exps:
            summaries[name] = Summary(
                params=fn.params, param_exps=best_exps, log_params=best_logs,
                file=fn.file, line=fn.line,
            )
    return summaries


# -- event walk ---------------------------------------------------------------


def _events(fn, costs, summaries):
    displays: dict[str, str] = {}
    yield from _walk(fn.body, [], frozenset(), fn, costs, summaries, displays)


def _walk(nodes, stack, bound, fn, costs, summaries, displays):
    for node in nodes:
        if isinstance(node, Loop):
            inner_bound = bound | set(node.target_names)
            if node.root_name is not None and node.root_name in bound:
                # iterating something derived from an enclosing loop's variable:
                # element traversal (e.g. rows of a matrix), not a product
                yield from _walk(node.body, stack, inner_bound, fn, costs, summaries, displays)
            elif node.size_symbol is None:
                yield _Event("unknown_loop", node, stack, [], [], displays)
                yield from _walk(node.body, stack, inner_bound, fn, costs, summaries, displays)
            elif node.size_symbol == CONST:
                yield from _walk(node.body, stack, inner_bound, fn, costs, summaries, displays)
            else:
                displays.setdefault(node.size_symbol, node.display)
                new_stack = stack + [node]
                yield _Event("loops", node, new_stack, _stack_counts(new_stack), [], displays)
                yield from _walk(node.body, new_stack, inner_bound, fn, costs, summaries, displays)
        elif isinstance(node, Op):
            cls = costs.lookup(node.kind, node.recv_kind) if costs else None
            if cls in (None, CONST_COST):
                continue
            base = _stack_counts(stack)
            contrib: Counts
            logs: list[str] = []
            if cls == QUADRATIC_GROWTH or node.recv_sym == GROWN:
                contrib = _stack_counts(stack)  # receiver size tracks iterations
                if cls == NLOGN:
                    logs = [s for s, _ in contrib]
            elif node.recv_sym in (None, CONST):
                contrib = []
            else:
                displays.setdefault(node.recv_sym, node.recv_display)
                contrib = [(node.recv_sym, 1)]
                if cls == NLOGN:
                    logs = [node.recv_sym]
            if contrib:
                yield _Event(
                    "op", node, stack, _merge(base, contrib), logs, displays,
                    extra={"cls": cls},
                )
        elif isinstance(node, Call):
            if node.callee == fn.name or node.callee.endswith("." + fn.name):
                yield _Event("recursion", node, stack, [], [], displays)
            elif summaries is not None:
                if "." in node.callee:
                    prefix, name = node.callee.rsplit(".", 1)
                    # a method on an arbitrary object is NOT the project
                    # function of the same name (html.replace != lib.replace)
                    if prefix not in ("self", "cls"):
                        continue
                else:
                    name = node.callee
                summary = summaries.get(name)
                if summary is None:
                    continue
                contrib = []
                logs = []
                arg_names = []
                for i, param in enumerate(summary.params):
                    exp = summary.param_exps.get(param)
                    if exp is None or i >= len(node.arg_syms):
                        continue
                    sym = node.arg_syms[i]
                    if sym in (None, CONST):
                        continue
                    displays.setdefault(sym, node.arg_displays[i])
                    contrib.append((sym, exp))
                    arg_names.append(node.arg_displays[i])
                    if param in summary.log_params:
                        logs.append(sym)
                if contrib:
                    yield _Event(
                        "call", node, stack, _merge(_stack_counts(stack), contrib),
                        logs, displays,
                        extra={"summary": summary, "arg_names": arg_names, "name": name},
                    )


def _stack_counts(stack: list[Loop]) -> Counts:
    out: Counts = []
    for loop in stack:
        out = _merge(out, [(loop.size_symbol, 1)])
    return out


def _merge(base: Counts, extra: Counts) -> Counts:
    out = list(base)
    for sym, exp in extra:
        for i, (s, e) in enumerate(out):
            if s == sym:
                out[i] = (s, e + exp)
                break
        else:
            out.append((sym, exp))
    return out


def _degree(counts: Counts) -> int:
    return sum(e for _, e in counts)


# -- findings ------------------------------------------------------------------


def _render(counts: Counts, logs: list[str]) -> tuple[str, str]:
    """Return (complexity string, severity)."""
    letters = {}
    for i, (sym, _) in enumerate(counts):
        letters[sym] = _LETTERS[i] if i < len(_LETTERS) else f"x{i}"
    parts = [f"{letters[s]}^{e}" if e > 1 else letters[s] for s, e in counts]
    parts += [f"log {letters[s]}" for s in logs if s in letters]
    severity = HIGH if any(e > 1 for _, e in counts) else MED
    return f"O({'*'.join(parts)})", severity


def _loops_finding(ev: _Event, fn: Function) -> Finding:
    complexity, severity = _render(ev.counts, ev.logs)
    lines = ", ".join(str(l.line) for l in ev.stack)
    names = ", ".join(f"`{ev.displays[s]}`" for s, _ in ev.counts)
    return Finding(
        file=fn.file, line=ev.stack[-1].line, function=fn.name,
        severity=severity, complexity=complexity,
        message=f"nested loops over {names} (lines {lines})",
        chain=tuple(l.line for l in ev.stack),
    )


def _cost_finding(ev: _Event, fn: Function) -> Finding:
    complexity, severity = _render(ev.counts, ev.logs)
    node = ev.node
    if ev.etype == "call":
        summary = ev.extra["summary"]
        args = ", ".join(f"`{a}`" for a in ev.extra["arg_names"])
        what = (
            f"call to `{ev.extra['name']}()` ({summary.file}:{summary.line}) "
            f"scales with {args}"
        )
    elif node.kind == "contains":
        what = f"`{node.display}` — linear membership scan of list `{node.recv_display}`"
        if node.recv_sym == GROWN:
            what += " (which grows inside the loop); hint: use a set"
        else:
            what += "; hint: use a set"
    elif node.kind == "str_concat":
        what = f"`{node.display}` — string concatenation rebuilds `{node.recv_display}` each iteration"
    elif ev.extra.get("cls") == NLOGN:
        what = f"`{node.display}` — sorts `{node.recv_display}` (n log n) each iteration"
    else:
        what = f"`{node.display}` — linear in `{node.recv_display}` each call"
    if ev.stack:
        loops = ", ".join(f"`{l.display}`" for l in ev.stack)
        what += f"; inside loop over {loops}"
    return Finding(
        file=fn.file, line=node.line, function=fn.name,
        severity=severity, complexity=complexity, message=what,
    )


def _dedup_prefixes(findings: list[Finding]) -> list[Finding]:
    """A depth-3 nest fires at depth 2 and 3; keep only the deepest finding."""
    chains = [f.chain for f in findings if f.chain]
    out = []
    for f in findings:
        if f.chain and any(
            len(c) > len(f.chain) and c[: len(f.chain)] == f.chain for c in chains
        ):
            continue
        out.append(f)
    return out
