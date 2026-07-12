"""M2 corpus: realistic code patterns across the class spectrum."""
import heapq


# --- O(log n) -----------------------------------------------------------------

def sum_of_digits(n: int) -> int:
    acc = 0
    while n:
        acc += n % 10
        n //= 10
    return acc


# --- O(n) ---------------------------------------------------------------------

def max_scan(xs: list[int]) -> int:
    best = None
    for x in xs:
        if best is None or x > best:
            best = x
    return best if best is not None else 0


def join_words(words: list[str]) -> str:
    return "".join(words)


def zip_sum(a: list[int], b: list[int]) -> list[int]:
    return [x + y for x, y in zip(a, b)]


def merge_sorted(a: list[int], b: list[int]) -> list[int]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def dedup_with_set(xs: list[int]) -> list[int]:
    seen = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def heapify_copy(xs: list[int]) -> list[int]:
    out = list(xs)
    heapq.heapify(out)
    return out


def matrix_row_sums(rows: list[list[int]]) -> list[int]:
    return [sum(r) for r in rows]


def intersect_sets(a: set[int], xs: list[int]) -> int:
    count = 0
    for x in xs:
        if x in a:
            count += 1
    return count


def range_sum(n: int) -> int:
    acc = 0
    for i in range(n):
        acc += i
    return acc


def sliding_window_max3(xs: list[int]) -> list[int]:
    return [max(xs[i:i + 3]) for i in range(max(0, len(xs) - 2))]


# --- O(n^2) ---------------------------------------------------------------------

def repeated_membership(xs: list[int]) -> int:
    count = 0
    for x in xs:
        if x in xs:      # linear scan per element: the classic accident
            count += 1
    return count


def dedup_quadratic(xs: list[int]) -> list[int]:
    out: list[int] = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def prepend_concat(words: list[str]) -> str:
    s = ""
    for w in words:
        s = w + s       # prepend forces a full copy every iteration
    return s


def bubble_sort(xs: list[int]) -> list[int]:
    out = list(xs)
    for i in range(len(out)):
        for j in range(len(out) - 1 - i):
            if out[j] > out[j + 1]:
                out[j], out[j + 1] = out[j + 1], out[j]
    return out


def selection_sort(xs: list[int]) -> list[int]:
    out = list(xs)
    for i in range(len(out)):
        k = i
        for j in range(i + 1, len(out)):
            if out[j] < out[k]:
                k = j
        out[i], out[k] = out[k], out[i]
    return out


def count_inversions(xs: list[int]) -> int:
    count = 0
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            if xs[i] > xs[j]:
                count += 1
    return count
