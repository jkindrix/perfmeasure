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
from perf_lint.ir import Call, Function, Loop

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
{callers_section}
Classify the finding:
- ACTIONABLE: the complexity claim is correct AND the collections involved can \
plausibly grow with real data (records, users, input items) — worth reporting.
- BENIGN: technically correct, but the collections are structurally small or \
bounded (fixed config tables, enum sets, template fields), so nobody would act on it.
- WRONG: the analyzer's reasoning is factually incorrect (loops don't multiply as \
claimed, the operation is not linear, early exit bounds the work). If the real \
cost is the same or WORSE than claimed, the finding is ACTIONABLE, not WRONG.

Answer with ONLY this JSON, nothing else:
{{"verdict": "ACTIONABLE" | "BENIGN" | "WRONG", "reason": "<one sentence>"}}"""


@dataclass
class Verdict:
    label: str  # ACTIONABLE | BENIGN | WRONG | UNADJUDICATED
    reason: str

    @property
    def keep(self) -> bool:
        # Suppress on BENIGN only. Empirically (evals/, 2026-07-11) every false
        # suppression came from a WRONG verdict — models hallucinating that the
        # analyzer's math is wrong — while BENIGN verdicts were never wrong.
        # WRONG therefore keeps the finding, annotated as disputed.
        return self.label != BENIGN


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


def build_prompt(
    finding: Finding, callers: list[tuple[Function, int]] | None = None
) -> str | None:
    source = _function_source(finding)
    if source is None:
        return None
    start, end, text = source
    return _PROMPT.format(
        file=finding.file, line=finding.line, severity=finding.severity,
        complexity=finding.complexity, function=finding.function,
        message=finding.message, start=start, end=end, source=text,
        callers_section=_callers_section(finding.function, callers),
    )


def build_caller_index(
    functions: list[Function],
) -> dict[str, list[tuple[Function, int]]]:
    """Map bare function name -> call sites (caller function, line)."""
    index: dict[str, list[tuple[Function, int]]] = {}
    for fn in functions:
        for call in _iter_calls(fn.body):
            name = call.callee.rsplit(".", 1)[-1]
            if name != fn.name:  # recursion isn't caller context
                index.setdefault(name, []).append((fn, call.line))
    return index


def _iter_calls(nodes):
    for node in nodes:
        if isinstance(node, Call):
            yield node
        elif isinstance(node, Loop):
            yield from _iter_calls(node.body)


def _callers_section(
    function: str, callers: list[tuple[Function, int]] | None
) -> str:
    if not callers:
        return ""
    parts = []
    for fn, line in callers[:3]:
        try:
            with open(fn.file, encoding="utf8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        lo = max(1, line - 2)
        snippet = "\n".join(lines[lo - 1 : line + 2])
        parts.append(f"{fn.file}:{line} in `{fn.name}`:\n```\n{snippet}\n```")
    if not parts:
        return ""
    return (
        f"\nCall sites of `{function}` found in the project "
        "(context for how big its inputs get):\n" + "\n".join(parts) + "\n"
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
    findings: list[Finding],
    client: LLMClient,
    functions: list[Function] | None = None,
) -> list[tuple[Finding, Verdict]]:
    caller_index = build_caller_index(functions) if functions else {}
    out: list[tuple[Finding, Verdict]] = []
    for f in findings:
        if f.severity == UNKNOWN:
            out.append((f, Verdict(UNADJUDICATED, "unknown verdicts are not adjudicated")))
            continue
        prompt = build_prompt(f, caller_index.get(f.function))
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
