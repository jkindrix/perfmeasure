"""Ground-truth corpus: functions with known time/space complexity.

All functions are pure (no argument mutation) — mutation handling lands in
M2. Expected classes live in evals/expected.json; the eval harness measures
every public function here and scores the tool's answers.
"""
from typing import Callable


# --- O(1) ---------------------------------------------------------------------

def first_element(xs: list[int]) -> int:
    return xs[0] if xs else 0


def is_even(n: int) -> bool:
    return n % 2 == 0


# --- O(log n) -----------------------------------------------------------------

def binary_search(xs: list[int], target: int) -> int:
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def halving_steps(n: int) -> int:
    steps = 0
    while n > 1:
        n //= 2
        steps += 1
    return steps


# --- O(n) ---------------------------------------------------------------------

def total(xs: list[int]) -> int:
    acc = 0
    for x in xs:
        acc += x
    return acc


def copy_list(xs: list[int]) -> list[int]:
    return list(xs)


def grow_linear(xs: list[int]) -> list[int]:
    return [x * 2 for x in xs]


def reverse_string(s: str) -> str:
    return s[::-1]


def dict_get_all(d: dict[str, int], keys: list[str]) -> int:
    acc = 0
    for k in keys:
        acc += d.get(k, 0)
    return acc


# --- O(n log n) -----------------------------------------------------------------

def sort_values(xs: list[int]) -> list[int]:
    return sorted(xs)


def unique_sorted(xs: list[int]) -> list[int]:
    return sorted(set(xs))


# --- O(n^2) ---------------------------------------------------------------------

def insertion_sort(xs: list[int]) -> list[int]:
    out = list(xs)
    for i in range(1, len(out)):
        key = out[i]
        j = i - 1
        while j >= 0 and out[j] > key:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = key
    return out


def contains_duplicate_quadratic(xs: list[int]) -> bool:
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            if xs[i] == xs[j]:
                return True
    return False


def count_char_pairs(s: str) -> int:
    count = 0
    for a in s:
        for b in s:
            if a == b:
                count += 1
    return count


def build_prefix_lists(xs: list[int]) -> list[list[int]]:
    acc = []
    for i in range(len(xs)):
        acc.append(xs[:i])
    return acc


# --- O(n^3) ---------------------------------------------------------------------

def count_triples(xs: list[int]) -> int:
    count = 0
    for a in xs:
        for b in xs:
            for c in xs:
                if (a + b + c) % 7 == 0:
                    count += 1
    return count


# --- O(2^n) ---------------------------------------------------------------------

def count_subsets(n: int) -> int:
    count = 0
    for mask in range(1 << n):
        count += mask & 1
    return count


def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


# --- UNDRIVABLE by design ---------------------------------------------------------

def untyped_mystery(xs):
    return len(xs)


def takes_callback(f: Callable[[int], int], xs: list[int]) -> list[int]:
    return [f(x) for x in xs]


# --- discriminating-class mass (the corpus was 51% O(n); these classes
# --- are where the fitter earns its keep, so they get real representation)

def third_steps(n: int) -> int:
    steps = 0
    while n > 1:
        n //= 3
        steps += 1
    return steps


def heap_drain(xs: list[int]) -> list[int]:
    import heapq
    h = list(xs)
    heapq.heapify(h)
    return [heapq.heappop(h) for _ in range(len(h))]


def rank_positions(xs: list[int]) -> list[int]:
    return sorted(range(len(xs)), key=xs.__getitem__)


def max_triple_product(xs: list[int]) -> int:
    best = 0
    for a in xs:
        for b in xs:
            for c in xs:
                best = max(best, a * b * c)
    return best


def masks_with_bit(n: int) -> int:
    count = 0
    for mask in range(1 << n):
        if mask & 1:
            count += 1
    return count
