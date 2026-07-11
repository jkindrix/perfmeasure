from __future__ import annotations

from perf_lint.analysis import HIGH, MED, UNKNOWN, Finding

_ORDER = {HIGH: 0, MED: 1, UNKNOWN: 2}


def render(findings: list[Finding], verbose: bool = False) -> str:
    shown = [f for f in findings if verbose or f.severity != UNKNOWN]
    shown.sort(key=lambda f: (f.file, f.line, _ORDER[f.severity]))

    lines = ["perf-lint report:", ""]
    for f in shown:
        lines.append(
            f"{f.file}:{f.line} [{f.severity}] {f.complexity} — "
            f"in {f.function}: {f.message}"
        )
    if shown:
        lines.append("")

    high = sum(1 for f in findings if f.severity == HIGH)
    med = sum(1 for f in findings if f.severity == MED)
    unknown = sum(1 for f in findings if f.severity == UNKNOWN)
    if high or med:
        summary = f"{high + med} finding{'s' if high + med != 1 else ''} ({high} high, {med} medium)"
    else:
        summary = "No findings."
    if unknown and not verbose:
        summary += f" — {unknown} unanalyzed (rerun with --verbose)"
    lines.append(summary)
    return "\n".join(lines)
