"""Live conformance: the generated Rust harness speaks the same protocol
as the Python runner. Skipped when cargo is unavailable; the harness build
is cache-keyed, so re-runs are fast."""
import shutil
from pathlib import Path

import pytest

from perfmeasure import protocol
from perfmeasure.languages.rust.plugin import RustPlugin
from perfmeasure.session import RunnerSession

CRATE = Path(__file__).parent / "fixtures" / "tiny_crate"

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None,
                                reason="cargo not installed")


@pytest.fixture(scope="module")
def session():
    plugin = RustPlugin()
    plugin.prepare(CRATE, log=lambda m: None)
    s = RunnerSession(plugin.runner_command(CRATE))
    yield s
    s.close()


def _call(session, fid, inputs, **kw):
    return session.request(
        protocol.call_msg(session.next_id(), fid, inputs, **kw), timeout=30)


def test_hello_capabilities(session):
    _call(session, "tiny_crate::head",
          [{"spec_type": "list_int", "shape": "random", "size": 4, "seed": 1}])
    caps = session.hello["capabilities"]
    assert session.hello["language"] == "rust"
    assert caps["memory"] == "counting_allocator"
    assert caps["discover"] is False


def test_call_timings_and_allocator(session):
    resp = _call(session, "tiny_crate::sort_copy",
                 [{"spec_type": "list_int", "shape": "random",
                   "size": 4096, "seed": 7}],
                 measure=["time", "memory"])
    assert resp["op"] == "result"
    assert all(t > 0 for t in resp["wall_seconds"])
    # sort_copy clones 4096 i64s: the counting allocator must see >= 32 KiB
    assert resp["peak_alloc_bytes"] >= 8 * 4096


def test_panic_is_structured_error(session):
    resp = _call(session, "tiny_crate::always_panics",
                 [{"spec_type": "list_int", "shape": "random",
                   "size": 4, "seed": 1}])
    assert resp["op"] == "error"
    assert resp["kind"] == "exception"
    assert "panicked" in resp["message"]
    # and the runner keeps serving after a panic
    ok = _call(session, "tiny_crate::head",
               [{"spec_type": "list_int", "shape": "random",
                 "size": 4, "seed": 1}])
    assert ok["op"] == "result"


def test_unknown_fid(session):
    resp = _call(session, "tiny_crate::nope", [])
    assert resp["op"] == "error" and resp["kind"] == "not_found"
