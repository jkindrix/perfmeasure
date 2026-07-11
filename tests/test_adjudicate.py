import textwrap

from perf_lint.adapters.python import PythonAdapter
from perf_lint.adjudicate import (
    ACTIONABLE,
    BENIGN,
    UNADJUDICATED,
    Verdict,
    adjudicate,
    build_prompt,
    parse_verdict,
)
from perf_lint.analysis import analyze_function, build_summaries
from perf_lint.costs import load_costs


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def complete(self, prompt):
        if self.error:
            raise self.error
        return self.response


def _finding(tmp_path):
    src = textwrap.dedent("""
        def find_dupes(items):
            for a in items:
                for b in items:
                    pass
    """)
    path = tmp_path / "mod.py"
    path.write_text(src)
    fns = PythonAdapter().parse(str(path), src.encode())
    costs = load_costs("python")
    findings = [
        f for fn in fns
        for f in analyze_function(fn, costs, build_summaries(fns, costs))
    ]
    assert len(findings) == 1
    return findings[0]


def test_parse_clean_json():
    v = parse_verdict('{"verdict": "BENIGN", "reason": "fixed table"}')
    assert v.label == BENIGN and not v.keep


def test_parse_json_wrapped_in_prose():
    v = parse_verdict(
        'Let me think. The loops multiply.\n'
        'Answer: {"verdict": "ACTIONABLE", "reason": "grows with users"}'
    )
    assert v.label == ACTIONABLE and v.keep


def test_parse_garbage_returns_none():
    assert parse_verdict("I am not sure about this one.") is None
    assert parse_verdict('{"verdict": "MAYBE"}') is None


def test_transport_error_fails_open(tmp_path):
    f = _finding(tmp_path)
    judged = adjudicate([f], FakeClient(error=ConnectionError("down")))
    (kept, verdict), = judged
    assert verdict.label == UNADJUDICATED
    assert verdict.keep


def test_benign_verdict_suppresses(tmp_path):
    f = _finding(tmp_path)
    judged = adjudicate([f], FakeClient('{"verdict": "BENIGN", "reason": "tiny"}'))
    (_, verdict), = judged
    assert not verdict.keep


def test_wrong_verdict_keeps(tmp_path):
    # models hallucinate WRONG verdicts; only BENIGN may suppress
    f = _finding(tmp_path)
    judged = adjudicate([f], FakeClient('{"verdict": "WRONG", "reason": "no nest"}'))
    (_, verdict), = judged
    assert verdict.keep


def test_prompt_contains_function_source(tmp_path):
    f = _finding(tmp_path)
    prompt = build_prompt(f)
    assert "def find_dupes" in prompt
    assert "O(n^2)" in prompt
