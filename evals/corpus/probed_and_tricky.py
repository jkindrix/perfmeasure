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
