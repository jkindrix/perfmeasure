"""Receiver-scaling corpus: methods whose cost depends on RECEIVER size.

Measured against empty default instances these all read O(1) — the
empty-receiver vacuity the fill machinery exists to fix. Ground truth is
the class in receiver size n.
"""
import bisect


class SortedVec:
    def __init__(self, items=None):
        self._xs = sorted(items) if items is not None else []

    def __len__(self):
        return len(self._xs)

    def add(self, value: int) -> None:
        bisect.insort(self._xs, value)          # O(log n) search + O(n) shift

    def rank(self, value: int) -> int:
        return bisect.bisect_left(self._xs, value)   # O(log n)

    def sum_items(self) -> int:
        return sum(self._xs)                    # O(n)


class IntBag:
    def __init__(self):
        self._s = set()

    def __len__(self):
        return len(self._s)

    def add(self, value: int) -> None:
        self._s.add(value)                      # O(1) amortized

    def has(self, value: int) -> bool:
        return value in self._s                 # O(1) expected
