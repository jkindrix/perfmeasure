from __future__ import annotations

from dataclasses import dataclass

from perf_lint.ir import CONST, Call, Function, Loop, Node

HIGH = "HIGH"
MED = "MED"
UNKNOWN = "UNKNOWN"

_LETTERS = "nmkpqr"


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


def analyze_function(fn: Function) -> list[Finding]:
    findings: list[Finding] = []
    _walk(fn.body, [], frozenset(), fn, findings)
    return _dedup_prefixes(findings)


def _walk(
    nodes: list[Node],
    stack: list[Loop],  # enclosing loops that count toward the product
    bound: frozenset[str],  # names bound by any enclosing loop
    fn: Function,
    findings: list[Finding],
) -> None:
    for node in nodes:
        if isinstance(node, Loop):
            inner_bound = bound | set(node.target_names)
            if node.root_name is not None and node.root_name in bound:
                # iterating something derived from an enclosing loop's variable:
                # element traversal (e.g. rows of a matrix), not a product
                _walk(node.body, stack, inner_bound, fn, findings)
            elif node.size_symbol is None:
                findings.append(Finding(
                    file=fn.file, line=node.line, function=fn.name,
                    severity=UNKNOWN, complexity="?",
                    message="while-loop bound depends on runtime data — not analyzed",
                ))
                _walk(node.body, stack, inner_bound, fn, findings)
            elif node.size_symbol == CONST:
                _walk(node.body, stack, inner_bound, fn, findings)
            else:
                new_stack = stack + [node]
                if len(new_stack) >= 2:
                    findings.append(_poly_finding(new_stack, fn))
                _walk(node.body, new_stack, inner_bound, fn, findings)
        elif isinstance(node, Call):
            if node.callee == fn.name or node.callee.endswith("." + fn.name):
                findings.append(Finding(
                    file=fn.file, line=node.line, function=fn.name,
                    severity=UNKNOWN, complexity="?",
                    message=f"recursive call to `{node.callee}` — recursion not analyzed",
                ))


def _poly_finding(stack: list[Loop], fn: Function) -> Finding:
    order: list[str] = []
    counts: dict[str, int] = {}
    displays: dict[str, str] = {}
    for loop in stack:
        sym = loop.size_symbol
        assert sym is not None and sym != CONST
        if sym not in counts:
            order.append(sym)
            displays[sym] = loop.display
        counts[sym] = counts.get(sym, 0) + 1

    parts = []
    for i, sym in enumerate(order):
        letter = _LETTERS[i] if i < len(_LETTERS) else f"x{i}"
        parts.append(f"{letter}^{counts[sym]}" if counts[sym] > 1 else letter)
    complexity = f"O({'*'.join(parts)})"

    severity = HIGH if any(c > 1 for c in counts.values()) else MED
    lines = ", ".join(str(l.line) for l in stack)
    names = ", ".join(f"`{displays[s]}`" for s in order)
    return Finding(
        file=fn.file, line=stack[-1].line, function=fn.name,
        severity=severity, complexity=complexity,
        message=f"nested loops over {names} (lines {lines})",
        chain=tuple(l.line for l in stack),
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
