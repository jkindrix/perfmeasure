"""Regressions for the three externally-reported P0s: stale Rust cache,
stdlib-shadowed imports, identical co-scaled inputs."""
from pathlib import Path

from perfmeasure import protocol
from perfmeasure.core.model import FunctionDescriptor, ParamInfo
from perfmeasure.core.planner import plan
from perfmeasure.languages.python.plugin import PythonPlugin
from perfmeasure.languages.rust.harness_gen import cache_key
from perfmeasure.session import RunnerSession

FIXTURES = Path(__file__).parent / "fixtures"


def test_co_scaled_params_get_distinct_inputs():
    d = FunctionDescriptor(
        fid="f.py::f", file="f", line=1, drivable=True,
        params=[ParamInfo("a", "list_int"), ParamInfo("b", "list_int")])
    p, _ = plan(d)
    s1, s2 = p.specs("random", 64)
    assert s1.seed != s2.seed


def test_cache_key_ignores_signatures_and_template(tmp_path):
    # staleness is cargo's job: the key must be stable per (crate, features)
    # so `cargo build` always runs against the same incremental dir
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
    assert cache_key(tmp_path, []) == cache_key(tmp_path, [])
    assert cache_key(tmp_path, []) != cache_key(tmp_path, ["full"])


def test_stdlib_shadowing_file_loads_the_actual_file():
    plugin = PythonPlugin()
    session = RunnerSession(plugin.runner_command(FIXTURES))
    try:
        target = str(FIXTURES / "random.py")
        resp = session.request(
            protocol.discover_msg(session.next_id(), [target]), timeout=30)
        fns = {f["fid"].rpartition("::")[2]: f for f in resp["functions"]}
        assert "marker_function" in fns, resp
        assert fns["marker_function"]["drivable"] is True
    finally:
        session.close()
