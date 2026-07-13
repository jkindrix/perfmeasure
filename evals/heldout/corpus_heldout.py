"""HELD-OUT accuracy corpus — v1 (sealed 2026-07-13).

The calibration gate (evals/harness.py) scores the same corpus the
fitter's thresholds were tuned on; this file is the independent check.
Rules that keep it independent:

- Expected classes live in expected-heldout.json with a citation per
  case (CPython docs, module sources, standard literature) — the labels
  are external, not authored to match the tool.
- Labels were written down BEFORE perfmeasure ever scored this file
  (pre-registered). The only pre-freeze run allowed is a provenance-only
  drivability smoke (`heldout.py --smoke` prints no classes), to catch
  wrapper authoring bugs while staying blind to accuracy.
- Results are reported verbatim. If a failure here ever motivates a
  fitter or threshold change, the consulted case moves into the
  calibration corpus and is replaced here; the version above bumps. A
  held-out set used for tuning is training data and must be retired.
"""
import base64
import hashlib
import heapq
import json
import random
import re
import statistics
from bisect import bisect_left, insort
from collections import Counter, deque
from itertools import accumulate

_Z40 = re.compile("z" * 40)     # cannot occur in generated a-z text


# --- O(1) ---------------------------------------------------------------------

def first_plus_last(xs: list[int]) -> int:
    return xs[0] + xs[-1]


def text_length(s: str) -> int:
    return len(s)


# --- O(log n) -----------------------------------------------------------------

def bisect_probe(xs: list[int]) -> int:
    return bisect_left(xs, 7)


def int_bisection(n: int) -> int:
    lo, hi, steps = 0, max(1, n), 0
    while lo < hi:
        mid = (lo + hi) // 2
        if mid * mid < n:
            lo = mid + 1
        else:
            hi = mid
        steps += 1
    return steps


# --- O(n) ---------------------------------------------------------------------

def count_sevens(xs: list[int]) -> int:
    return xs.count(7)


def find_impossible(s: str) -> int:
    return s.find("\x00")


def split_on_a(s: str) -> int:
    return len(s.split("a"))


def counter_build(xs: list[int]) -> int:
    return len(Counter(xs))


def deque_build(xs: list[int]) -> int:
    return len(deque(xs))


def set_build(xs: list[int]) -> int:
    return len(set(xs))


def accumulate_list(xs: list[int]) -> list[int]:
    return list(accumulate(xs))


def sha256_digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def b64_roundtrip_len(data: bytes) -> int:
    return len(base64.b64encode(data))


def json_dump_len(xs: list[int]) -> int:
    return len(json.dumps(xs))


def shuffle_copy(xs: list[int]) -> list[int]:
    out = list(xs)
    random.Random(12345).shuffle(out)
    return out


def insort_copy(xs: list[int]) -> int:
    out = list(xs)
    insort(out, 0)
    return len(out)


def dict_values_sum(d: dict[int, int]) -> int:
    return sum(d.values())


def heapify_min(xs: list[int]) -> int:
    h = list(xs)
    heapq.heapify(h)
    return h[0]


def regex_scan(s: str) -> bool:
    return _Z40.search(s) is not None


# --- O(n log n) ---------------------------------------------------------------

def sorted_by_abs(xs: list[int]) -> list[int]:
    return sorted(xs, key=abs)


def counter_ranked(xs: list[int]) -> int:
    return len(Counter(xs).most_common())


def nsmallest_half(xs: list[int]) -> list[int]:
    return heapq.nsmallest(max(1, len(xs) // 2), xs)


def median_value(xs: list[int]) -> float:
    return statistics.median(xs)


# --- O(n^2) -------------------------------------------------------------------

def closest_pair_brute(xs: list[int]) -> int:
    best = None
    for i, a in enumerate(xs):
        for b in xs[i + 1:]:
            d = abs(a - b)
            if best is None or d < best:
                best = d
    return best or 0


# --- O(n^3) -------------------------------------------------------------------

def triples_summing_to_seven(xs: list[int]) -> int:
    count = 0
    for a in xs:
        for b in xs:
            for c in xs:
                count += (a + b + c) == 7
    return count


# --- O(2^n) -------------------------------------------------------------------

def full_binary_walk(n: int) -> int:
    if n <= 0:
        return 1
    return full_binary_walk(n - 1) + full_binary_walk(n - 1)
