from __future__ import annotations

import argparse
import os
import sys

from perf_lint.adapters import ADAPTERS
from perf_lint.analysis import UNKNOWN, Finding, analyze_function
from perf_lint.report import render

SKIP_DIRS = {"__pycache__"}


def collect_files(paths: list[str]) -> list[str]:
    exts = tuple(e for a in ADAPTERS for e in a.extensions)
    files: list[str] = []
    for path in paths:
        if os.path.isfile(path):
            if path.endswith(exts):
                files.append(path)
        else:
            for root, dirs, names in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
                files.extend(
                    os.path.join(root, n) for n in sorted(names) if n.endswith(exts)
                )
    return files


def run(paths: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in collect_files(paths):
        adapter = next(a for a in ADAPTERS if path.endswith(a.extensions))
        with open(path, "rb") as f:
            source = f.read()
        for fn in adapter.parse(path, source):
            findings.extend(analyze_function(fn))
    return findings


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="perf-lint",
        description="Flag likely-accidental O(n^2)+ code.",
    )
    ap.add_argument("paths", nargs="+", help="files or directories to analyze")
    ap.add_argument(
        "-v", "--verbose", action="store_true",
        help="also show UNKNOWN verdicts (unanalyzable loops, recursion)",
    )
    args = ap.parse_args()

    findings = run(args.paths)
    print(render(findings, verbose=args.verbose))
    sys.exit(1 if any(f.severity != UNKNOWN for f in findings) else 0)
