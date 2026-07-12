import textwrap

from perf_lint.adapters.rust import RustAdapter
from perf_lint.analysis import HIGH, MED, UNKNOWN, analyze_function, build_summaries
from perf_lint.costs import load_costs

COSTS = load_costs("rust")


def analyze(src: str):
    fns = RustAdapter().parse("test.rs", textwrap.dedent(src).encode())
    summaries = build_summaries(fns, COSTS)
    return [f for fn in fns for f in analyze_function(fn, COSTS, summaries)]


def test_nested_for_same_slice():
    findings = analyze("""
        fn find_dupes(items: &Vec<u32>) {
            for a in items {
                for b in items {
                    let _ = a == b;
                }
            }
        }
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_range_over_len_equates():
    findings = analyze("""
        fn scan(items: &Vec<u32>) {
            for a in items {
                for i in 0..items.len() {
                    let _ = i;
                }
            }
        }
    """)
    assert [f.severity for f in findings] == [HIGH]


def test_const_range_not_flagged():
    findings = analyze("""
        fn grid() {
            for i in 0..3 {
                for j in 0..3 {
                    let _ = i * j;
                }
            }
        }
    """)
    assert findings == []


def test_element_traversal_not_flagged():
    findings = analyze("""
        fn flatten(matrix: &Vec<Vec<u32>>) {
            for row in matrix {
                for x in row {
                    let _ = x;
                }
            }
        }
    """)
    assert findings == []


def test_vec_contains_on_grown_local():
    findings = analyze("""
        fn dedupe(items: &Vec<u32>) -> Vec<u32> {
            let mut seen = Vec::new();
            for x in items {
                if !seen.contains(x) {
                    seen.push(*x);
                }
            }
            seen
        }
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_hashset_contains_not_flagged():
    findings = analyze("""
        fn dedupe(items: &Vec<u32>) {
            let mut seen = HashSet::new();
            for x in items {
                if !seen.contains(x) {
                    seen.insert(*x);
                }
            }
        }
    """)
    assert findings == []


def test_vec_param_contains_via_type_annotation():
    findings = analyze("""
        fn check(items: &Vec<u32>, allowed: &Vec<u32>) {
            for x in items {
                if allowed.contains(x) {
                    let _ = x;
                }
            }
        }
    """)
    assert [f.severity for f in findings] == [MED]
    assert findings[0].complexity == "O(n*m)"


def test_iterator_chain_inside_loop():
    findings = analyze("""
        fn pairs(users: &Vec<u32>) {
            for u in users {
                let hit = users.iter().any(|v| v == u);
            }
        }
    """)
    assert [f.severity for f in findings] == [HIGH]
    assert findings[0].complexity == "O(n^2)"


def test_chain_alone_not_flagged():
    findings = analyze("""
        fn names(users: &Vec<String>) -> Vec<String> {
            users.iter().map(|u| u.clone()).collect::<Vec<String>>()
        }
    """)
    assert findings == []


def test_nested_chains_flagged():
    findings = analyze("""
        fn matches(xs: &Vec<u32>, ys: &Vec<u32>) -> usize {
            xs.iter().filter(|x| ys.iter().any(|y| y == *x)).count()
        }
    """)
    assert [f.severity for f in findings] == [MED]
    assert findings[0].complexity == "O(n*m)"


def test_flatten_chain_emits_single_loop():
    # `for x in expr.into_iter().flatten()` must produce exactly one loop node,
    # not one from the for + one from re-detecting the inner sub-chain
    findings = analyze("""
        fn process(data: &Vec<u8>, parser: &mut Parser) {
            for byte in data {
                for result in parser.parse(*byte).into_iter().flatten() {
                    let _ = result;
                }
            }
        }
    """)
    assert len(findings) == 1  # was 2 before flatten was recognized


def test_chain_consumed_by_next_is_constant():
    # into_iter().next() takes one element — O(1), not a pass over rows
    findings = analyze("""
        fn first_rows(cases: &Vec<u32>, rows: Vec<u32>) {
            for case in cases {
                let row = rows.clone().into_iter().next();
                let major = "1.2.3".split('.').next();
            }
        }
    """)
    assert findings == []


def test_zip_argument_is_not_nested():
    # zip iterates its argument in lockstep — linear, not quadratic
    findings = analyze("""
        fn pairs(text: &str) -> usize {
            text.chars()
                .zip(text.chars().skip(1))
                .filter(|&(a, b)| a == b)
                .count()
        }
    """)
    assert findings == []


def test_sort_inside_loop():
    findings = analyze("""
        fn ranks(xs: &Vec<u32>, ys: &mut Vec<u32>) {
            for x in xs {
                ys.sort();
            }
        }
    """)
    assert [f.severity for f in findings] == [MED]
    assert findings[0].complexity == "O(n*m*log m)"


def test_string_concat_not_flagged():
    # Rust String += is amortized O(append), unlike Python
    findings = analyze("""
        fn join(items: &Vec<String>) -> String {
            let mut s = String::new();
            for x in items {
                s += x;
            }
            s
        }
    """)
    assert findings == []


def test_while_let_unknown():
    findings = analyze("""
        fn drain(queue: &mut Vec<u32>) {
            while let Some(x) = queue.pop() {
                let _ = x;
            }
        }
    """)
    assert [f.severity for f in findings] == [UNKNOWN]


def test_recursion_unknown():
    findings = analyze("""
        fn fib(n: u64) -> u64 {
            if n < 2 { return n; }
            fib(n - 1) + fib(n - 2)
        }
    """)
    assert {f.severity for f in findings} == {UNKNOWN}


def test_linear_helper_called_in_loop():
    findings = analyze("""
        fn scan(users: &Vec<u32>, id: u32) -> bool {
            for u in users {
                if *u == id { return true; }
            }
            false
        }

        fn enrich(users: &Vec<u32>) {
            for u in users {
                let _ = scan(users, *u);
            }
        }
    """)
    flagged = [f for f in findings if f.function == "enrich"]
    assert [f.severity for f in flagged] == [HIGH]
    assert flagged[0].complexity == "O(n^2)"
