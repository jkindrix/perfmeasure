"""Live conformance: the real Python runner, spoken to over the real
protocol via a RunnerSession. This is what keeps the stdlib-only runner's
hand-rolled wire format in sync with perfmeasure/protocol.py."""
from pathlib import Path

import pytest

from perfmeasure import protocol
from perfmeasure.languages.python.plugin import PythonPlugin
from perfmeasure.session import RunnerSession

FIXTURE = str(Path(__file__).parent / "fixtures" / "sample_target.py")


@pytest.fixture(scope="module")
def session():
    plugin = PythonPlugin()
    s = RunnerSession(plugin.runner_command(Path(FIXTURE).parent))
    yield s
    s.close()


def _discover(session, only=None):
    resp = session.request(
        protocol.discover_msg(session.next_id(), [FIXTURE], only), timeout=30)
    assert resp["op"] == "result"
    return {f["fid"].rpartition("::")[2]: f for f in resp["functions"]}


def test_hello_capabilities(session):
    _discover(session)  # forces spawn + handshake
    caps = session.hello["capabilities"]
    assert session.hello["language"] == "python"
    assert "list_int" in caps["spec_types"]
    assert caps["memory"] == "tracemalloc"


def test_discover_types_and_reasons(session):
    fns = _discover(session)
    assert fns["linear"]["drivable"] is True
    assert fns["linear"]["params"][0]["spec_type"] == "list_int"
    assert fns["untyped"]["drivable"] is False
    assert "missing annotation" in fns["untyped"]["skip_reason"]


def test_call_returns_timings_and_memory(session):
    fns = _discover(session)
    resp = session.request(protocol.call_msg(
        session.next_id(), fns["linear"]["fid"],
        [{"spec_type": "list_int", "shape": "sorted", "size": 256, "seed": 7}],
        measure=["time", "memory"]), timeout=30)
    assert resp["op"] == "result"
    assert resp["repeats_done"] >= 1
    assert all(t > 0 for t in resp["wall_seconds"])
    assert resp["peak_alloc_bytes"] >= 0


def test_exception_is_structured_error(session):
    fns = _discover(session)
    resp = session.request(protocol.call_msg(
        session.next_id(), fns["rejects_everything"]["fid"],
        [{"spec_type": "list_int", "shape": "random", "size": 4, "seed": 1}]),
        timeout=30)
    assert resp["op"] == "error"
    assert resp["kind"] == "exception"
    assert "nope" in resp["message"]


def test_target_stdout_cannot_corrupt_protocol(session):
    fns = _discover(session)
    resp = session.request(protocol.call_msg(
        session.next_id(), fns["prints_to_stdout"]["fid"],
        [{"spec_type": "list_int", "shape": "random", "size": 8, "seed": 1}]),
        timeout=30)
    assert resp["op"] == "result"


def test_unknown_function_is_not_found(session):
    resp = session.request(protocol.call_msg(
        session.next_id(), FIXTURE + "::does_not_exist",
        [{"spec_type": "list_int", "shape": "random", "size": 4, "seed": 1}]),
        timeout=30)
    assert resp["op"] == "error"
    assert resp["kind"] == "not_found"


def test_warmup_zero_still_detects_mutation(session):
    """Lean calls (warmup=0) must not silently reuse dirtied inputs:
    mutation is detected from the first timed call instead."""
    fns = _discover(session)
    resp = session.request(protocol.call_msg(
        session.next_id(), fns["sorts_in_place"]["fid"],
        [{"spec_type": "list_int", "shape": "random", "size": 512, "seed": 3}],
        warmup=0, max_repeats=3), timeout=30)
    assert resp["op"] == "result"
    assert resp["mutates"] is True
    assert resp["batched"] is False


def test_known_mutates_hint_is_honored(session):
    fns = _discover(session)
    resp = session.request(protocol.call_msg(
        session.next_id(), fns["sorts_in_place"]["fid"],
        [{"spec_type": "list_int", "shape": "random", "size": 512, "seed": 3}],
        warmup=0, max_repeats=2, known_mutates=True), timeout=30)
    assert resp["op"] == "result"
    assert resp["mutates"] is True
    assert resp["batched"] is False


def _find_py39():
    import shutil
    import subprocess
    if shutil.which("uv"):
        r = subprocess.run(["uv", "python", "find", "3.9"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return shutil.which("python3.9")


@pytest.mark.skipif(_find_py39() is None, reason="no Python 3.9 available")
def test_discovery_on_py39_with_builtin_generics():
    # 3.9/3.10 quirk: isinstance(list[int], type) is True, so an unguarded
    # issubclass() against os.PathLike raised TypeError and killed the
    # runner mid-discovery — every 3.9 target using PEP 585 hints crashed
    plugin = PythonPlugin(python=_find_py39())
    s = RunnerSession(plugin.runner_command(Path(FIXTURE).parent))
    try:
        resp = s.request(
            protocol.discover_msg(s.next_id(), [FIXTURE], None), timeout=30)
        assert resp["op"] == "result", resp
        fns = {f["fid"].rpartition("::")[2]: f for f in resp["functions"]}
        assert fns["linear"]["drivable"] is True
        assert s.hello["runtime"].startswith("CPython 3.9")
    finally:
        s.close()
