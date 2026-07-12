"""Runner lifecycle: spawn, handshake, request/response, hard timeout,
crash recovery, per-function blacklist.

Crash-is-data policy: a hung call gets SIGKILL and comes back as a
synthesized {"kind": "timeout_hard"} error; a dead runner comes back as
{"kind": "runner_crash"}. The session restarts the runner lazily and
blacklists a fid after CRASH_LIMIT crashes attributed to it.
"""
from __future__ import annotations

import queue
import subprocess
import threading
from collections import deque

from perfmeasure import protocol

CRASH_LIMIT = 2
HANDSHAKE_TIMEOUT_S = 30.0
STDERR_TAIL_LINES = 20


class RunnerSession:
    def __init__(self, argv: list[str], cwd: str | None = None):
        self.argv = argv
        self.cwd = cwd
        self.hello: dict = {}
        self._proc: subprocess.Popen | None = None
        self._out: queue.Queue | None = None
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._crashes: dict[str, int] = {}
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
        if hello is None or hello.get("op") != "hello":
            raise RuntimeError(
                f"runner failed to start: {self._crash_detail()}")
        if hello.get("protocol") != protocol.PROTOCOL_VERSION:
            raise RuntimeError(f"protocol mismatch: {hello.get('protocol')}")
        self.hello = hello

    def _pump(self, stream, is_stdout: bool) -> None:
        out, proc = self._out, self._proc
        for line in stream:
            if is_stdout:
                out.put(line)
            else:
                self._stderr_tail.append(line.rstrip())
        if is_stdout and out is self._out:
            out.put(None)  # EOF sentinel, ignored after restart

    def _read(self, timeout: float) -> dict | None:
        try:
            line = self._out.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return protocol.parse_msg(line)
        except ValueError:
            return None

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
            if resp is None:
                if self._alive():  # genuine hang
                    self._kill()
                    if fid:
                        self._crashes[fid] = self._crashes.get(fid, 0) + 1
                    return protocol.error_result(
                        msg.get("id", "?"), fid, "timeout_hard",
                        f"no response within {timeout:.0f}s; runner killed",
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
