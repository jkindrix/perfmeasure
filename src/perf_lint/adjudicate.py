"""LLM adjudication of findings: suppress-only, fail-open.

The adjudicator reviews each HIGH/MED finding with the enclosing function's
source and returns ACTIONABLE / BENIGN / WRONG. It can only suppress findings,
never create them; any transport or parse failure keeps the finding.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from perf_lint.adapters import ADAPTERS
from perf_lint.analysis import UNKNOWN, Finding

ACTIONABLE = "ACTIONABLE"
BENIGN = "BENIGN"
WRONG = "WRONG"
UNADJUDICATED = "UNADJUDICATED"

MAX_SOURCE_LINES = 120

_PROMPT = """\
A static complexity analyzer flagged this code. Your job is to judge the finding, \
not to fix the code.

Finding: {file}:{line} [{severity}] {complexity} — in {function}: {message}

Source of `{function}` ({file}, lines {start}-{end}):
```
{source}
```

Classify the finding:
- ACTIONABLE: the complexity claim is correct AND the collections involved can \
plausibly grow with real data (records, users, input items) — worth reporting.
- BENIGN: technically correct, but the collections are structurally small or \
bounded (fixed config tables, enum sets, template fields), so nobody would act on it.
- WRONG: the analyzer's reasoning is factually incorrect (loops don't multiply as \
claimed, the operation is not linear, early exit bounds the work).

Answer with ONLY this JSON, nothing else:
{{"verdict": "ACTIONABLE" | "BENIGN" | "WRONG", "reason": "<one sentence>"}}"""


@dataclass
class Verdict:
    label: str  # ACTIONABLE | BENIGN | WRONG | UNADJUDICATED
    reason: str

    @property
    def keep(self) -> bool:
        return self.label in (ACTIONABLE, UNADJUDICATED)


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


def build_prompt(finding: Finding) -> str | None:
    source = _function_source(finding)
    if source is None:
        return None
    start, end, text = source
    return _PROMPT.format(
        file=finding.file, line=finding.line, severity=finding.severity,
        complexity=finding.complexity, function=finding.function,
        message=finding.message, start=start, end=end, source=text,
    )


def parse_verdict(response: str) -> Verdict | None:
    # reasoning models may wrap the JSON in prose/thinking; take the last
    # parseable object that carries a known verdict
    for m in reversed(re.findall(r"\{[^{}]*\}", response)):
        try:
            obj = json.loads(m)
        except ValueError:
            continue
        label = str(obj.get("verdict", "")).upper()
        if label in (ACTIONABLE, BENIGN, WRONG):
            return Verdict(label=label, reason=str(obj.get("reason", "")))
    return None


def adjudicate(
    findings: list[Finding], client: LLMClient
) -> list[tuple[Finding, Verdict]]:
    out: list[tuple[Finding, Verdict]] = []
    for f in findings:
        if f.severity == UNKNOWN:
            out.append((f, Verdict(UNADJUDICATED, "unknown verdicts are not adjudicated")))
            continue
        prompt = build_prompt(f)
        if prompt is None:
            out.append((f, Verdict(UNADJUDICATED, "could not extract function source")))
            continue
        try:
            response = client.complete(prompt)
        except Exception as e:  # fail open: transport errors keep the finding
            out.append((f, Verdict(UNADJUDICATED, f"llm error: {e}")))
            continue
        verdict = parse_verdict(response)
        out.append((f, verdict or Verdict(UNADJUDICATED, "unparseable llm response")))
    return out


def _function_source(finding: Finding) -> tuple[int, int, str] | None:
    adapter = next(
        (a for a in ADAPTERS if finding.file.endswith(a.extensions)), None
    )
    if adapter is None:
        return None
    try:
        with open(finding.file, "rb") as fh:
            source = fh.read()
    except OSError:
        return None
    best = None
    for fn in adapter.parse(finding.file, source):
        if fn.name == finding.function and fn.line <= finding.line <= fn.end_line:
            if best is None or fn.line > best.line:  # innermost match
                best = fn
    if best is None:
        return None
    lines = source.decode("utf8", errors="replace").splitlines()
    end = min(best.end_line, best.line + MAX_SOURCE_LINES - 1)
    return best.line, end, "\n".join(lines[best.line - 1 : end])
