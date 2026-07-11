import os
import textwrap

from perf_lint.adapters.python import PythonAdapter
from perf_lint.analysis import HIGH, MED, UNKNOWN, analyze_function

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def analyze(src: str):
    fns = PythonAdapter().parse("test.py", textwrap.dedent(src).encode())
    return [f for fn in fns for f in analyze_function(fn)]


def analyze_fixture(name: str):
    path = os.path.join(FIXTURES, name)
    with open(path, "rb") as f:
        source = f.read()
    fns = PythonAdapter().parse(path, source)
    return [f for fn in fns for f in analyze_function(fn)]


def test_quadratic_same_list_fixture():
    findings = analyze_fixture("quadratic_same_list.py")
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == HIGH
    assert f.complexity == "O(n^2)"
    assert f.function == "find_dupes"
    assert f.line == 4


def test_benign_grid_fixture():
    assert analyze_fixture("benign_grid.py") == []


def test_different_collections_is_medium():
    findings = analyze("""
        def pair(xs, ys):
            for x in xs:
                for y in ys:
                    print(x, y)
    """)
    assert [f.severity for f in findings] == [MED]
    assert findings[0].complexity == "O(n*m)"


def test_element_traversal_not_flagged():
    findings = analyze("""
        def flatten(matrix):
            out = []
            for row in matrix:
                for x in row:
                    out.append(x)
            return out
    """)
    assert findings == []


def test_range_len_equates_with_direct_iteration():
    findings = analyze("""
        def scan(items):
            for i in range(len(items)):
                for x in items:
                    print(i, x)
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_triple_nest_reports_once():
    findings = analyze("""
        def cube(xs):
            for a in xs:
                for b in xs:
                    for c in xs:
                        print(a, b, c)
    """)
    assert len(findings) == 1
    assert findings[0].complexity == "O(n^3)"
    assert findings[0].severity == HIGH


def test_nested_comprehension_flagged():
    findings = analyze("""
        def pairs(xs):
            return [(a, b) for a in xs for b in xs]
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_while_loop_is_unknown():
    findings = analyze("""
        def drain(queue):
            while queue:
                queue.pop()
    """)
    assert [f.severity for f in findings] == [UNKNOWN]


def test_recursion_is_unknown():
    findings = analyze("""
        def fib(n):
            if n < 2:
                return n
            return fib(n - 1) + fib(n - 2)
    """)
    assert {f.severity for f in findings} == {UNKNOWN}
    assert all("recursive" in f.message for f in findings)


def test_single_loop_not_flagged():
    findings = analyze("""
        def total(items):
            s = 0
            for x in items:
                s += x
            return s
    """)
    assert findings == []
