"""Project scan: walk, discover, measure everything, summarize coverage.

Discovery is requested one file at a time so a module whose import crashes
the runner costs that file, not the scan. The coverage summary is part of
the honesty contract: undrivable functions are counted and their reasons
histogrammed, never silently skipped.
"""
from __future__ import annotations

import fnmatch
import os
import re
from collections import Counter

from perfmeasure import protocol
from perfmeasure.core.ladder import Budget
from perfmeasure.core.model import FunctionReport
from perfmeasure.core.orchestrator import measure_function
from perfmeasure.session import RunnerSession

SKIP_DIRS = {"__pycache__", "node_modules", "target", "build", "dist",
             "tests", "test", "venv"}
_TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest)\.py$")


def collect_files(paths: list[str], extensions: tuple[str, ...],
                  exclude: list[str] | None = None) -> list[str]:
    """Walk targets; test dirs/files are excluded by default — they are
    denominator noise for a coverage report, same policy as Rust's
    #[cfg(test)] filter."""
    files: list[str] = []
    for path in paths:
        if os.path.isfile(path):
            if path.endswith(extensions):
                files.append(path)
        else:
            for root, dirs, names in os.walk(path):
                dirs[:] = [d for d in dirs
                           if not d.startswith(".") and d not in SKIP_DIRS]
                files.extend(os.path.join(root, n) for n in sorted(names)
                             if n.endswith(extensions)
                             and not _TEST_FILE.match(n))
    if exclude:
        files = [f for f in files
                 if not any(fnmatch.fnmatch(f, pat) or pat in f
                            for pat in exclude)]
    return files


def scan(session: RunnerSession, files: list[str], budget: Budget,
         progress=None) -> tuple[list[FunctionReport], dict]:
    reports: list[FunctionReport] = []
    import_failures: list[str] = []
    from perfmeasure.cli import _descriptor  # shared wire->model mapping
    for file in files:
        resp = session.request(
            protocol.discover_msg(session.next_id(), [os.path.abspath(file)]),
            timeout=60.0)
        if resp["op"] == "error":
            import_failures.append(f"{file}: {resp['kind']}")
            continue
        for raw in resp["functions"]:
            desc = _descriptor(raw)
            if desc.skip_reason and desc.skip_reason.startswith("import_failed"):
                import_failures.append(file)
                continue
            if progress:
                progress(desc.fid)
            reports.append(measure_function(session, desc, budget))
    summary = _summarize(reports, import_failures)
    return reports, summary


def _summarize(reports: list[FunctionReport],
               import_failures: list[str]) -> dict:
    provenance = Counter(r.provenance for r in reports)
    reasons = Counter(
        (r.provenance_detail or "").split(":")[0]
        for r in reports if r.provenance == "UNDRIVABLE")
    measured = provenance["MEASURED"] + provenance["AMBIGUOUS"]
    return {
        "functions": len(reports),
        "measured": measured,
        "provenance": dict(provenance),
        "undrivable_reasons": dict(reasons),
        "import_failures": import_failures,
    }


def render_summary(summary: dict) -> str:
    lines = [
        f"\n{summary['measured']}/{summary['functions']} functions measured"
        f" ({', '.join(f'{k}: {v}' for k, v in summary['provenance'].items())})"
    ]
    if summary["undrivable_reasons"]:
        reasons = ", ".join(f"{k or 'unknown'}: {v}" for k, v in
                            sorted(summary["undrivable_reasons"].items(),
                                   key=lambda kv: -kv[1]))
        lines.append(f"undrivable reasons: {reasons}")
    if summary["import_failures"]:
        lines.append(f"import failures: {len(summary['import_failures'])} "
                     f"file(s): " + ", ".join(summary["import_failures"][:5]))
    return "\n".join(lines)
