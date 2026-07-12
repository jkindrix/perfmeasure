"""Runner lifecycle: spawn, handshake, request/response, hard timeout,
crash recovery, per-function blacklist.

Crash-is-data policy: a hung call gets SIGKILL and comes back as a
synthesized {"kind": "timeout_hard"} error; a dead runner comes back as
{"kind": "runner_crash"}. The session restarts the runner lazily and
blacklists a fid after CRASH_LIMIT crashes attributed to it. Timeouts
never blacklist: a hang is a steepness signal about the measurement
(data), while a crash is a defect in the runner or target — only the
latter justifies refusing further calls. Timeouts are tallied separately
for diagnostics.
"""
from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections import deque

from perfmeasure import protocol

CRASH_LIMIT = 2
HANDSHAKE_TIMEOUT_S = 30.0
STDERR_TAIL_LINES = 20
_EOF = object()     # runner stdout closed — a crash signal, never a hang


class RunnerSession:
    def __init__(self, argv: list[str], cwd: str | None = None):
        self.argv = argv
        self.cwd = cwd
        self.hello: dict = {}
        self._proc: subprocess.Popen | None = None
        self._out: queue.Queue | None = None
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._crashes: dict[str, int] = {}
        self._timeouts: dict[str, int] = {}
        self._req = 0

    # -- lifecycle ------------------------------------------------------------

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            self.argv, cwd=self.cwd, text=True, bufsize=1,
            errors="replace",   # target code may emit raw bytes on stderr
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._out = queue.Queue()
        threading.Thread(target=self._pump, args=(self._proc.stdout, True),
                         daemon=True).start()
        threading.Thread(target=self._pump, args=(self._proc.stderr, False),
                         daemon=True).start()
        hello = self._read(HANDSHAKE_TIMEOUT_S)
        if not isinstance(hello, dict) or hello.get("op") != "hello":
            raise RuntimeError(
                f"runner failed to start: {self._crash_detail()}")
        if hello.get("protocol") != protocol.PROTOCOL_VERSION:
            raise RuntimeError(f"protocol mismatch: {hello.get('protocol')}")
        self.hello = hello

    def _pump(self, stream, is_stdout: bool) -> None:
        out = self._out
        for line in stream:
            if is_stdout:
                out.put(line)
            else:
                self._stderr_tail.append(line.rstrip())
        if is_stdout and out is self._out:
            out.put(None)  # EOF sentinel, ignored after restart

    def _read(self, timeout: float):
        """One protocol message, None on hang, or _EOF when the runner's
        stdout closed. EOF and timeout MUST stay distinguishable: stdout
        closes before poll() observes the exit, and a crash read as a
        hang would evade the crash blacklist. Non-protocol lines
        (a third-party runner letting stray output reach fd 1) are logged
        and skipped rather than read as a hang — SIGKILLing a healthy
        runner over one stray print would be a fragile contract. The
        timeout bounds the TOTAL wait, stray lines included."""
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._out.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                return _EOF
            try:
                return protocol.parse_msg(line)
            except ValueError:
                self._stderr_tail.append(
                    f"[non-protocol stdout] {line.rstrip()[:200]}")

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()

    def _crash_detail(self) -> dict:
        code = self._proc.poll() if self._proc else None
        return {"exit_code": code, "stderr_tail": list(self._stderr_tail)}

    def close(self) -> None:
        if self._alive():
            try:
                protocol.write_msg(self._proc.stdin, {"op": "shutdown"})
                self._proc.wait(timeout=2)
            except Exception:
                self._kill()

    # -- requests ---------------------------------------------------------------

    def blacklisted(self, fid: str) -> bool:
        return self._crashes.get(fid, 0) >= CRASH_LIMIT

    def request(self, msg: dict, timeout: float) -> dict:
        """Send one request, await its response. Never raises for target-code
        failures: hangs and crashes come back as error dicts."""
        fid = msg.get("fid")
        if fid and self.blacklisted(fid):
            return protocol.error_result(
                msg.get("id", "?"), fid, "runner_crash",
                f"blacklisted after {CRASH_LIMIT} crashes")
        if not self._alive():
            self._start()
        try:
            protocol.write_msg(self._proc.stdin, msg)
        except (BrokenPipeError, OSError):
            return self._crashed(msg)
        while True:
            resp = self._read(timeout)
            if resp is _EOF:
                # stdout closed mid-request: the runner is dying. Wait
                # briefly so the crash detail carries the real exit code
                # instead of racing poll().
                try:
                    self._proc.wait(timeout=2.0)
                except Exception:
                    pass
                return self._crashed(msg)
            if resp is None:
                if self._alive():  # genuine hang
                    self._kill()
                    n = 1
                    if fid:
                        n = self._timeouts[fid] = \
                            self._timeouts.get(fid, 0) + 1
                    return protocol.error_result(
                        msg.get("id", "?"), fid, "timeout_hard",
                        f"no response within {timeout:.0f}s; runner killed "
                        f"(timeout #{n} for this function)",
                        self._crash_detail())
                return self._crashed(msg)
            if resp.get("id") == msg.get("id"):
                return resp
            # stale line from a killed predecessor request: drop it

    def _crashed(self, msg: dict) -> dict:
        fid = msg.get("fid")
        if fid:
            self._crashes[fid] = self._crashes.get(fid, 0) + 1
        detail = self._crash_detail()
        self._kill()
        return protocol.error_result(
            msg.get("id", "?"), fid, "runner_crash",
            f"runner died (exit {detail['exit_code']})", detail)

    def next_id(self) -> str:
        self._req += 1
        return f"r{self._req}"
