"""Language-neutral IR. Adapters emit this; the analysis engine consumes only this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

# Size symbol for loops whose iteration count doesn't scale with input
# (literal collections, range over integer literals).
CONST = "CONST"


@dataclass
class Call:
    callee: str  # dotted best-effort name, e.g. "helper" or "self.helper"
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
    body: list[Node] = field(default_factory=list)


Node = Union[Loop, Call]
