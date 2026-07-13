"""Regressions for the external review's P1 batch: budget deadline,
cargo-always-runs, scan JSON summary, and SEMANTIC shape properties
(tag-presence parity alone let dict_si mean str keys in Python and i64
keys in Rust)."""
import sys
import time
from pathlib import Path

import perfmeasure.languages.rust.harness_gen as hg
from perfmeasure.cli import measure_target, scan_target
from perfmeasure.core.ladder import Budget

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "perfmeasure"
                       / "languages" / "python" / "runner_files"))
import runner as pyrunner  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def test_budget_is_a_deadline():
    t0 = time.perf_counter()
    reports, _ = measure_target(str(FIXTURES / "sample_target.py"), "linear",
                                Budget(per_function_s=0.75))
    wall = time.perf_counter() - t0
    assert wall < 3.0, f"budget 0.75 took {wall:.2f}s"
    assert reports[0].provenance in ("MEASURED", "AMBIGUOUS")


def test_deadline_is_hard_even_against_slow_calls():
    """budget + rescue is the absolute wall; a function whose every call
    sleeps must not stretch it (the old formula floored request timeouts
    at 1s + 2s grace regardless of a tiny budget)."""
    budget = Budget(per_function_s=0.2, rescue_s=0.5)
    t0 = time.perf_counter()
    measure_target(str(FIXTURES / "sample_target.py"), "slow_sleeper", budget)
    wall = time.perf_counter() - t0
    # generous process overhead allowance; the pre-fix behavior was >2s
    # of request timeout alone on a 0.06s budget
    assert wall < 0.2 + 0.5 + 1.2, f"hard wall breached: {wall:.2f}s"


def test_memory_traced_on_first_five_sizes():
    reports, _ = measure_target(str(FIXTURES / "sample_target.py"), "linear",
                                Budget(per_function_s=1.5))
    shape = reports[0].per_shape[0]
    first5 = shape.points[:5]
    assert all(p.peak_bytes is not None for p in first5), \
        "the fitter needs >= 5 memory points; the first five sizes must trace"


def test_cargo_runs_even_when_binary_exists(tmp_path, monkeypatch):
    calls = []

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        calls.append(argv)
        return FakeProc()

    monkeypatch.setattr(hg, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(hg.subprocess, "run", fake_run)
    (tmp_path / "crate").mkdir()
    (tmp_path / "crate" / "Cargo.toml").write_text('[package]\nname="x"\n')
    harness = tmp_path / "v2" / hg.cache_key(tmp_path / "crate", [])
    (harness / "target" / "release").mkdir(parents=True)
    (harness / "target" / "release" / "perfmeasure_harness").write_text("")
    hg.build_harness(tmp_path / "crate", "x", [], log=lambda m: None)
    assert calls and calls[0][0] == "cargo", \
        "a pre-existing binary must NOT skip cargo (stale-binary P0)"


def test_scan_returns_summary():
    _, _, summary = scan_target(str(FIXTURES), Budget(per_function_s=0.5))
    assert summary["functions"] > 0
    assert "provenance" in summary


def _mat(tag, shape, size=256, seed=7):
    return pyrunner.materialize(
        {"spec_type": tag, "shape": shape, "size": size, "seed": seed})


def test_shape_semantics_dup_heavy_pools_are_small():
    assert len(set(_mat("list_int", "dup_heavy"))) <= 256 // 8
    assert len(set(_mat("list_float", "dup_heavy"))) <= 256 // 8
    assert set(_mat("bytes_", "dup_heavy")) <= set(b"abcd")
    assert set(_mat("str_", "dup_heavy")) <= set("abcd")
    assert len(set(_mat("dict_ii", "dup_heavy").values())) <= 64


def test_shape_semantics_key_types_and_order():
    assert all(isinstance(k, int) for k in _mat("dict_ii", "random"))
    assert all(isinstance(k, str) for k in _mat("dict_si", "random"))
    xs = _mat("list_int", "sorted")
    assert xs == sorted(xs) and len(xs) == 256
    rs = _mat("list_int", "reversed")
    assert rs == sorted(rs, reverse=True)
    assert len(set(_mat("list_int", "all_equal"))) == 1
    assert len(_mat("bytes_", "random")) == 256


def test_fast_materializers_are_deterministic():
    assert _mat("list_int", "random") == _mat("list_int", "random")
    assert _mat("str_", "random") == _mat("str_", "random")


def test_set_fingerprint_sees_same_length_churn():
    # remove-one-add-one keeps len constant; a length-only fingerprint
    # classified the mutator as pure and re-timed dirtied state
    s = set(range(100))
    before = pyrunner._fingerprint(s)
    assert before == pyrunner._fingerprint(set(range(100)))  # stable
    s.discard(0)
    s.add(-1_000_003)
    assert pyrunner._fingerprint(s) != before


def test_dict_fingerprint_sees_deep_value_churn():
    # first-8-items sampling was blind to same-length mutation past the head
    d = {i: i for i in range(100)}
    before = pyrunner._fingerprint(d)
    assert before == pyrunner._fingerprint({i: i for i in range(100)})
    d[97] ^= 1
    changed = pyrunner._fingerprint(d) != before
    for k in list(d)[::3]:          # heavier churn must always be seen
        d[k] = d[k] ^ 1
    assert changed or pyrunner._fingerprint(d) != before
