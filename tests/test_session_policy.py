"""Session failure taxonomy: timeouts are data, crashes are defects.

A hang (timeout_hard) must never blacklist a fid — a slow-but-correct
function that times out per shape would otherwise have its remaining
shapes reported as synthetic runner_crash. Only genuine runner deaths
count toward CRASH_LIMIT. A timeout whose request window was clamped by
the per-function deadline is a scheduling fact, relabeled
deadline_exhausted by the orchestrator and reported as budget exhaustion,
not as a property of the function.
"""
import sys
import time
from pathlib import Path

from perfmeasure.cli import measure_target
from perfmeasure.core.ladder import Budget
from perfmeasure.session import RunnerSession

FIXTURES = Path(__file__).parent / "fixtures"

FAKE_RUNNER = """
import json, sys, time
print(json.dumps({"op": "hello", "protocol": 1, "language": "fake",
                  "runtime": "fake", "capabilities": {}}), flush=True)
mode = sys.argv[1]
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("op") == "shutdown":
        break
    if mode == "hang":
        time.sleep(3600)
    elif mode == "die":
        sys.exit(9)
"""


def _fake_session(mode: str) -> RunnerSession:
    return RunnerSession([sys.executable, "-c", FAKE_RUNNER, mode])


def test_timeouts_never_blacklist():
    session = _fake_session("hang")
    msg = {"op": "call", "id": "1", "fid": "f.py::slow"}
    for i in range(3):
        msg["id"] = str(i)
        resp = session.request(msg, timeout=0.3)
        assert resp["kind"] == "timeout_hard", \
            f"call {i} should time out, got {resp['kind']}: {resp['message']}"
    assert not session.blacklisted("f.py::slow")
    session.close()


def test_crashes_still_blacklist():
    session = _fake_session("die")
    msg = {"op": "call", "id": "1", "fid": "f.py::crashy"}
    for i in range(2):
        msg["id"] = str(i)
        resp = session.request(msg, timeout=5.0)
        assert resp["kind"] == "runner_crash"
    assert session.blacklisted("f.py::crashy")
    resp = session.request(dict(msg, id="3"), timeout=5.0)
    assert "blacklisted" in resp["message"]
    session.close()


def test_deadline_clamped_kill_reports_budget_not_function():
    """A function killed only because the budget window shrank must read
    as budget exhaustion, never as TIMEOUT-because-steep with a
    first-probe-timeout detail blaming the function."""
    budget = Budget(per_function_s=0.2, rescue_s=0.3)
    t0 = time.perf_counter()
    reports, _ = measure_target(str(FIXTURES / "sample_target.py"),
                                "slow_sleeper", budget)
    wall = time.perf_counter() - t0
    assert wall < 0.2 + 0.3 + 1.2
    r = reports[0]
    assert r.provenance == "TIMEOUT"
    assert "budget exhausted" in (r.provenance_detail or "")
