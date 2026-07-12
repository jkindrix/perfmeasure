from __future__ import annotations

import hashlib
import json
import re

from perf_lint.analysis import HIGH, MED, UNKNOWN, Finding

_ORDER = {HIGH: 0, MED: 1, UNKNOWN: 2}


def render(
    findings: list[Finding],
    verbose: bool = False,
    suppressed: list[tuple[Finding, str]] | None = None,
    title: str | None = None,
) -> str:
    shown = [f for f in findings if verbose or f.severity != UNKNOWN]
    shown.sort(key=lambda f: (f.file, f.line, _ORDER[f.severity]))

    lines = [title or "perf-lint report:", ""]
    for f in shown:
        lines.append(
            f"{f.file}:{f.line} [{f.severity}] {f.complexity} — "
            f"in {f.function}: {f.message}"
        )
    if shown:
        lines.append("")
    if suppressed and verbose:
        lines.append("suppressed by adjudication:")
        for f, reason in suppressed:
            lines.append(f"  {f.file}:{f.line} {f.complexity} — {reason}")
        lines.append("")

    high = sum(1 for f in findings if f.severity == HIGH)
    med = sum(1 for f in findings if f.severity == MED)
    unknown = sum(1 for f in findings if f.severity == UNKNOWN)
    if high or med:
        summary = f"{high + med} finding{'s' if high + med != 1 else ''} ({high} high, {med} medium)"
    else:
        summary = "No findings."
    if suppressed:
        summary += f" — {len(suppressed)} suppressed by adjudication"
        if not verbose:
            summary += " (--verbose to list)"
    if unknown and not verbose:
        summary += f" — {unknown} unanalyzed (rerun with --verbose)"
    lines.append(summary)
    return "\n".join(lines)


def finding_id(f: Finding) -> str:
    # line numbers stripped so ids survive unrelated edits above the finding
    stable = re.sub(r"\d+", "", f"{f.file}|{f.function}|{f.severity}|{f.complexity}|{f.message}")
    return hashlib.sha1(stable.encode()).hexdigest()[:12]


def render_json(findings: list[Finding], verbose: bool = False) -> str:
    shown = [f for f in findings if verbose or f.severity != UNKNOWN]
    shown.sort(key=lambda f: (f.file, f.line, _ORDER[f.severity]))
    return json.dumps(
        [
            {
                "id": finding_id(f),
                "file": f.file,
                "line": f.line,
                "function": f.function,
                "severity": f.severity,
                "complexity": f.complexity,
                "message": f.message,
            }
            for f in shown
        ],
        indent=2,
    )
