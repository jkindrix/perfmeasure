import os
import textwrap

from perf_lint.adapters.python import PythonAdapter
from perf_lint.analysis import HIGH, MED, UNKNOWN, analyze_function, build_summaries
from perf_lint.costs import load_costs

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
COSTS = load_costs("python")


def analyze(src: str):
    fns = PythonAdapter().parse("test.py", textwrap.dedent(src).encode())
    summaries = build_summaries(fns, COSTS)
    return [f for fn in fns for f in analyze_function(fn, COSTS, summaries)]


def analyze_fixture(name: str):
    path = os.path.join(FIXTURES, name)
    with open(path, "rb") as f:
        source = f.read()
    fns = PythonAdapter().parse(path, source)
    summaries = build_summaries(fns, COSTS)
    return [f for fn in fns for f in analyze_function(fn, COSTS, summaries)]


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


# -- M2: cost tables ---------------------------------------------------------


def test_membership_on_grown_list_is_quadratic():
    findings = analyze("""
        def dedupe(items):
            seen = []
            for x in items:
                if x in seen:
                    continue
                seen.append(x)
            return seen
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"
    assert "use a set" in findings[0].message


def test_membership_on_set_not_flagged():
    findings = analyze("""
        def dedupe(items):
            seen = set()
            for x in items:
                if x in seen:
                    continue
                seen.add(x)
            return seen
    """)
    assert findings == []


def test_membership_on_inferred_list_param():
    findings = analyze("""
        def merge(items, acc):
            for x in items:
                if x not in acc:
                    acc.append(x)
    """)
    assert [f.severity for f in findings] == [MED]
    assert findings[0].complexity == "O(n*m)"


def test_membership_unknown_type_is_silent():
    findings = analyze("""
        def check(items, allowed):
            for x in items:
                if x in allowed:
                    print(x)
    """)
    assert findings == []


def test_sorted_inside_loop():
    findings = analyze("""
        def ranks(xs):
            for x in xs:
                order = sorted(xs)
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2*log n)"


def test_string_concat_in_loop_is_quadratic():
    findings = analyze("""
        def join_all(items):
            s = ""
            for x in items:
                s += str(x)
            return s
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_insert_zero_in_loop():
    findings = analyze("""
        def rev(items, out):
            for x in items:
                out.insert(0, x)
    """)
    assert [f.severity for f in findings] == [MED]
    assert findings[0].complexity == "O(n*m)"


def test_pop_without_index_not_flagged():
    findings = analyze("""
        def drain(items, stack):
            for x in items:
                stack.pop()
    """)
    assert findings == []


def test_membership_on_string_literal_not_flagged():
    findings = analyze("""
        def vowels(text):
            count = 0
            for ch in text:
                if ch in "aeiou":
                    count += 1
            return count
    """)
    assert findings == []


# -- M2: call summaries --------------------------------------------------------


def test_linear_helper_called_in_loop():
    findings = analyze("""
        def find_by_id(users, uid):
            for u in users:
                if u.id == uid:
                    return u

        def enrich(users):
            for u in users:
                match = find_by_id(users, u.id)
    """)
    flagged = [f for f in findings if f.function == "enrich"]
    assert [f.severity for f in flagged] == [HIGH]
    assert flagged[0].complexity == "O(n^2)"
    assert "find_by_id" in flagged[0].message


def test_helper_not_flagged_outside_loop():
    findings = analyze("""
        def scan(users):
            for u in users:
                pass

        def once(users):
            scan(users)
    """)
    assert findings == []


def test_alias_makes_nesting_quadratic():
    findings = analyze("""
        def dupes(items):
            same = items
            for a in items:
                for b in same:
                    if a == b:
                        pass
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_reassigned_name_not_treated_as_alias():
    findings = analyze("""
        def scan(xs, ys):
            zs = xs
            zs = ys
            for a in xs:
                for b in zs:
                    pass
    """)
    # zs is reassigned -> ambiguous -> not an alias of xs -> independent (MED)
    assert [f.severity for f in findings] == [MED]


def test_method_call_does_not_match_global_function():
    findings = analyze("""
        def replace(template, ctx):
            for key in ctx:
                template = 1

        def render(pages, mapping):
            for page in pages:
                page.replace("a", mapping)
    """)
    assert [f for f in findings if f.function == "render"] == []


def test_self_method_call_resolves_to_summary():
    findings = analyze("""
        class Store:
            def scan(self, records):
                for r in records:
                    pass

            def refresh(self, records):
                for r in records:
                    self.scan(records)
    """)
    flagged = [f for f in findings if f.function == "refresh"]
    assert [f.severity for f in flagged] == [HIGH]
    assert flagged[0].complexity == "O(n^2)"


def test_ambiguous_helper_name_skipped():
    findings = analyze("""
        def scan(users):
            for u in users:
                pass

        def scan(items):
            for i in items:
                pass

        def caller(users):
            for u in users:
                scan(users)
    """)
    assert [f for f in findings if f.function == "caller"] == []
