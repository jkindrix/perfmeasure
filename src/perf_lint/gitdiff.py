"""Diff mode: report only findings not present at a baseline git revision.

Findings are matched by stable id computed over root-relative paths, so the
same finding in the baseline worktree and the working tree gets the same id.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from perf_lint.analysis import Finding
from perf_lint.report import finding_id


def _git(cwd: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", cwd, *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def repo_root(path: str) -> str:
    start = path if os.path.isdir(path) else os.path.dirname(path) or "."
    return _git(start, "rev-parse", "--show-toplevel")


def relative_id(f: Finding, root: str) -> str:
    rel = os.path.relpath(f.file, root)
    return finding_id(
        Finding(
            file=rel, line=f.line, function=f.function, severity=f.severity,
            complexity=f.complexity, message=f.message.replace(root + os.sep, ""),
        )
    )


def baseline_ids(rev: str, root: str, rel_paths: list[str], run) -> set[str]:
    """Analyze rel_paths at `rev` in a temporary worktree; return finding ids."""
    tmp = tempfile.mkdtemp(prefix="perf-lint-baseline-")
    _git(root, "worktree", "add", "--detach", "--quiet", tmp, rev)
    try:
        targets = [
            os.path.normpath(os.path.join(tmp, p)) for p in rel_paths
            if os.path.exists(os.path.join(tmp, p))
        ]
        findings, _functions = run(targets)
        return {relative_id(f, tmp) for f in findings}
    finally:
        _git(root, "worktree", "remove", "--force", tmp)


def new_findings(
    rev: str, paths: list[str], findings: list[Finding], run
) -> list[Finding]:
    root = repo_root(paths[0])
    rel_paths = [os.path.relpath(os.path.abspath(p), root) for p in paths]
    base = baseline_ids(rev, root, rel_paths, run)
    return [f for f in findings if relative_id(f, root) not in base]
