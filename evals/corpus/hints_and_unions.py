"""Hint-resolution classes: unions, float scalars, list[Any], and
TYPE_CHECKING-guarded aliases (the humanize pattern: get_type_hints fails
wholesale, annotations arrive as strings)."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeAlias

    Numberish: TypeAlias = float | str


def halving_float(value: float) -> int:
    steps = 0
    v = abs(float(value))
    while v > 1.0:
        v /= 2.0
        steps += 1
    return steps


def union_digits(value: int | str) -> int:
    n = abs(int(value))
    digits = 0
    while n:
        n //= 10
        digits += 1
    return digits


def sum_any(items: list[Any]) -> float:
    total = 0.0
    for it in items:
        total += float(it)
    return total


def alias_total(value: Numberish) -> float:
    try:
        total = 0.0
        for v in value:
            total += float(v)
        return total
    except TypeError:
        return float(value)


def alias_scaled(value: Numberish, repeat: int) -> float:
    total = 0.0
    for _ in range(max(1, int(repeat))):
        for v in value:
            total += float(v)
    return total
