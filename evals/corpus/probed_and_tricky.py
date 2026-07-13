"""M2 corpus: unhinted params (probing), mutators, memoization, fixed-int
fallback variants."""
from functools import lru_cache


# --- probing targets: no annotations on purpose --------------------------------

def untyped_total(xs):
    acc = 0
    for x in xs:
        acc += x
    return acc


def untyped_scale(n):
    acc = 0
    for i in range(n):
        acc += i
    return acc


def untyped_shout(text):
    return text.upper()


def probed_pairs(a, b):
    count = 0
    for x in a:
        for y in b:
            if x == y:
                count += 1
    return count


# --- mutators --------------------------------------------------------------------

def sort_in_place(xs: list[int]) -> None:
    xs.sort()


def reverse_in_place(xs: list[int]) -> None:
    xs.reverse()


# --- memoization -------------------------------------------------------------------

@lru_cache(maxsize=None)
def memo_fib(n: int) -> int:
    if n < 2:
        return n
    return memo_fib(n - 1) + memo_fib(n - 2)


# --- fixed-int fallback variants -----------------------------------------------------

def requires_zero(xs: list[int], flags: int) -> int:
    if flags != 0:
        raise ValueError("flags must be 0")
    acc = 0
    for x in xs:
        acc += x
    return acc


def needs_middle_index(xs: list[int], i: int) -> int:
    if not (len(xs) // 2 <= i < len(xs)):
        raise ValueError("index must be in the upper half")
    return xs[i]


def unprobeable(cfg):
    return cfg.host + ":" + str(cfg.port)   # no generated input satisfies this


# --- pass-through returns -----------------------------------------------------------

def passthrough(xs: list[int]) -> list[int]:
    return xs


# --- coefficient step: allocation doubles per element past a threshold. Truth
#     is O(n) space with a 2x scale step mid-ladder (the sort-scratch /
#     allocator-threshold pattern); the fitter must keep O(n) in the
#     candidates and flag the step, not promote to a clean O(n log n).
def step_alloc(xs: list[int]) -> bytearray:
    per = 8 if len(xs) <= 4096 else 16
    return bytearray(per * len(xs))


# --- same-length set churn: removes the 8 smallest, adds 8 new. len()
#     never moves, so a length-only fingerprint read this as pure and
#     re-timed dirtied state. Must flag mutates_input.
def set_churn(s: set[int]) -> int:
    for x in sorted(s)[:8]:
        s.discard(x)
        s.add(-x - 1_000_003)
    return len(s)


# --- same-length dict churn away from the head: flips values across the
#     whole dict without changing length or the first few items. Must
#     flag mutates_input.
def dict_value_churn(d: dict[int, int]) -> int:
    for k in list(d)[::3]:
        d[k] = d[k] ^ 1
    return len(d)
