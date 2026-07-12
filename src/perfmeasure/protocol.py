"""Wire protocol between core and language runners: JSON Lines over stdio.

This module is the single source of schema truth on the core side. Runners
are standalone programs (the Python runner is a stdlib-only file executed
under the TARGET project's interpreter, so it cannot import this module);
they implement the same wire format, held in sync by the conformance tests
in tests/test_runner_conformance.py.

Ops:
  runner -> core on start:   {"op": "hello", "protocol": 1, "language": ...,
                              "runtime": ..., "capabilities": {...}}
  core -> runner:            {"op": "discover", "id", "files": [...], "only": fid|null}
                             {"op": "call", "id", "fid", "inputs": [genspec...],
                              "warmup", "max_repeats", "min_total_ms",
                              "measure": ["time","memory"], "budget_ms"}
                             {"op": "ping", "id"} / {"op": "shutdown"}
  runner -> core:            {"op": "result", "id", ...} | {"op": "pong", "id"}
                             {"op": "error", "id", "fid", "kind", "message",
                              "detail": {...}, "retryable": bool}

Error kinds: exception | unsupported_input | not_found | import_failed
             | internal  (timeout_hard and runner_crash are synthesized
             core-side by session.py, never sent by a runner).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, IO

PROTOCOL_VERSION = 1

ERROR_KINDS = {
    "exception", "unsupported_input", "not_found", "import_failed",
    "internal", "timeout_hard", "runner_crash",
}


def seed_for(fid: str, shape: str, size: int) -> int:
    digest = hashlib.sha256(f"{fid}|{shape}|{size}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def write_msg(stream: IO[str], msg: dict[str, Any]) -> None:
    stream.write(json.dumps(msg, separators=(",", ":")) + "\n")
    stream.flush()


def parse_msg(line: str) -> dict[str, Any]:
    msg = json.loads(line)
    if not isinstance(msg, dict) or "op" not in msg:
        raise ValueError(f"malformed protocol message: {line[:200]}")
    return msg


def discover_msg(req_id: str, files: list[str], only: str | None = None) -> dict:
    return {"op": "discover", "id": req_id, "files": files, "only": only}


def call_msg(req_id: str, fid: str, inputs: list[dict], *, warmup: int = 1,
             max_repeats: int = 15, min_total_ms: int = 10,
             measure: list[str] | None = None, budget_ms: int = 10_000) -> dict:
    return {
        "op": "call", "id": req_id, "fid": fid, "inputs": inputs,
        "warmup": warmup, "max_repeats": max_repeats,
        "min_total_ms": min_total_ms,
        "measure": measure or ["time"], "budget_ms": budget_ms,
    }


def error_result(req_id: str, fid: str | None, kind: str, message: str,
                 detail: dict | None = None) -> dict:
    assert kind in ERROR_KINDS
    return {"op": "error", "id": req_id, "fid": fid, "kind": kind,
            "message": message, "detail": detail or {}, "retryable": False}
