"""Language-neutral IR. Adapters emit this; the analysis engine consumes only this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

# Size symbol for loops whose iteration count doesn't scale with input
# (literal collections, range over integer literals).
CONST = "CONST"

# Receiver marker for collections built up inside the loops that use them
# (e.g. `seen = []` + `seen.append(x)`): their size tracks the iteration count.
GROWN = "GROWN"


@dataclass
class Call:
    """A call that may resolve to a project function (for cost summaries)."""

    callee: str  # dotted best-effort name, e.g. "helper" or "self.helper"
    line: int
    arg_syms: list[str | None] = field(default_factory=list)  # positional args
    arg_displays: list[str] = field(default_factory=list)


@dataclass
class Op:
    """An operation with a table-defined cost (membership test, sort, ...)."""

    kind: str  # "contains" | "str_concat" | "method:<name>" | "function:<name>"
    recv_kind: str  # "list" | "set" | "dict" | "str" | "tuple" | "any" | "unknown"
    recv_sym: str | None  # size symbol of the receiver, CONST, or GROWN
    recv_display: str
    display: str  # source snippet for reports
    line: int


@dataclass
class Loop:
    kind: str  # "for" | "while" | "comprehension"
    # CONST, "size:<expr>", or None when the bound is data-dependent (unknowable).
    size_symbol: str | None
    display: str  # source text of the iterated expression, for reports
    root_name: str | None  # leftmost identifier of the iterated expression
    target_names: list[str]  # variables bound by this loop
    line: int
    body: list[Node] = field(default_factory=list)


@dataclass
class Function:
    name: str
    file: str
    line: int
    end_line: int = 0
    params: list[str] = field(default_factory=list)  # positional, self/cls dropped
    body: list[Node] = field(default_factory=list)


Node = Union[Loop, Call, Op]
