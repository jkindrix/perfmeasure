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
    if suppressed:
        # demote, don't hide: adjudication can wrongly suppress a real finding
        # (measured on Rust), so suppressed findings stay visible for review
        lines.append("suppressed by adjudication (review before trusting):")
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
        summary += f" — {len(suppressed)} suppressed by adjudication (listed above)"
    if unknown and not verbose:
        summary += f" — {unknown} unanalyzed (rerun with --verbose)"
    lines.append(summary)
    return "\n".join(lines)


def finding_id(f: Finding) -> str:
    # line numbers stripped so ids survive unrelated edits above the finding
    stable = re.sub(r"\d+", "", f"{f.file}|{f.function}|{f.severity}|{f.complexity}|{f.message}")
    return hashlib.sha1(stable.encode()).hexdigest()[:12]


def _finding_dict(f: Finding, suppressed_reason: str | None = None) -> dict:
    d = {
        "id": finding_id(f),
        "file": f.file,
        "line": f.line,
        "function": f.function,
        "severity": f.severity,
        "complexity": f.complexity,
        "message": f.message,
    }
    if suppressed_reason is not None:
        d["suppressed"] = True
        d["suppressed_reason"] = suppressed_reason
    return d


def render_json(
    findings: list[Finding],
    verbose: bool = False,
    suppressed: list[tuple[Finding, str]] | None = None,
) -> str:
    shown = [f for f in findings if verbose or f.severity != UNKNOWN]
    shown.sort(key=lambda f: (f.file, f.line, _ORDER[f.severity]))
    # demote, don't hide: suppressed findings stay in the JSON with a flag so
    # machine consumers (CI) can see a wrongly-suppressed true positive
    out = [_finding_dict(f) for f in shown]
    out += [_finding_dict(f, reason) for f, reason in (suppressed or [])]
    return json.dumps(out, indent=2)
