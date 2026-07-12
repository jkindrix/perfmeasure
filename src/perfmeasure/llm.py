"""LLM transport clients, salvaged from the perf-lint adjudicator.

Not wired into v1. Parked here for the planned coverage lever: LLM-generated
input harnesses for UNDRIVABLE functions (post-M3). `CommandClient` runs any
CLI LLM (prompt on stdin, response on stdout — e.g. `claude -p`, billed under
a subscription rather than per-token); `LLMClient` speaks the OpenAI-compatible
HTTP API (Ollama, vLLM, raw provider APIs).
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import urllib.request


class LLMClient:
    """Minimal OpenAI-compatible chat-completions client (Ollama, vLLM, ...)."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json"},
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"]


class CommandClient:
    """Runs a subprocess LLM: prompt on stdin, response on stdout."""

    def __init__(self, command: str, timeout: float = 120.0):
        self.argv = shlex.split(command)
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        result = subprocess.run(
            self.argv, input=prompt, capture_output=True, text=True,
            timeout=self.timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"command exited {result.returncode}: {result.stderr.strip()[:200]}"
            )
        return result.stdout


def last_json_object(response: str) -> dict | None:
    """Last parseable {...} in a possibly prose-wrapped LLM response."""
    for m in reversed(re.findall(r"\{[^{}]*\}", response)):
        try:
            obj = json.loads(m)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
