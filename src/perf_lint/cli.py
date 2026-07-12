from __future__ import annotations

import argparse
import fnmatch
import os
import sys

from perf_lint.adapters import ADAPTERS
from perf_lint.analysis import HIGH, MED, UNKNOWN, Finding, analyze_function, build_summaries
from perf_lint.config import Config, load_config
from perf_lint.costs import load_costs
from perf_lint.report import render, render_json

SKIP_DIRS = {"__pycache__", "target", "node_modules"}
IGNORE_MARKER = "perf-lint: ignore"


def collect_files(paths: list[str], exclude: list[str] | None = None) -> list[str]:
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
    if exclude:
        files = [
            f for f in files
            if not any(fnmatch.fnmatch(f, pat) or pat in f for pat in exclude)
        ]
    return files


def run(paths: list[str], exclude: list[str] | None = None) -> tuple[list[Finding], list]:
    # summaries and cost tables are per-language: a Rust fn must not match a
    # Python fn of the same name
    by_language: dict[str, list] = {}
    for path in collect_files(paths, exclude):
        adapter = next(a for a in ADAPTERS if path.endswith(a.extensions))
        with open(path, "rb") as f:
            source = f.read()
        by_language.setdefault(adapter.language, []).extend(adapter.parse(path, source))
    findings: list[Finding] = []
    functions = []
    for language, fns in by_language.items():
        costs = load_costs(language)
        summaries = build_summaries(fns, costs)
        for fn in fns:
            findings.extend(analyze_function(fn, costs, summaries))
        functions.extend(fns)
    return drop_suppressed(findings), functions


def drop_suppressed(findings: list[Finding]) -> list[Finding]:
    """Honor `perf-lint: ignore` comments on the finding line or the line above."""
    lines_cache: dict[str, list[str]] = {}
    kept = []
    for f in findings:
        if f.file not in lines_cache:
            try:
                with open(f.file, encoding="utf8", errors="replace") as fh:
                    lines_cache[f.file] = fh.read().splitlines()
            except OSError:
                lines_cache[f.file] = []
        lines = lines_cache[f.file]
        window = lines[max(0, f.line - 2) : f.line]
        if any(IGNORE_MARKER in ln for ln in window):
            continue
        kept.append(f)
    return kept


def _exit_code(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    if fail_on == "high":
        return 1 if any(f.severity == HIGH for f in findings) else 0
    return 1 if any(f.severity in (HIGH, MED) for f in findings) else 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="perf-lint",
        description="Flag likely-accidental O(n^2)+ code.",
    )
    ap.add_argument("paths", nargs="+", help="files or directories to analyze")
    ap.add_argument(
        "-v", "--verbose", action="store_true",
        help="also show UNKNOWN verdicts and adjudication-suppressed findings",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--diff", metavar="REV",
        help="report only findings not present at git revision REV",
    )
    ap.add_argument(
        "--fail-on", choices=["high", "med", "never"], default=None,
        help="findings severity that produces exit code 1 (default: med)",
    )
    ap.add_argument(
        "--adjudicate", action="store_true",
        help="review findings with an LLM and suppress benign ones "
        "(suppress-only; fails open)",
    )
    ap.add_argument(
        "--llm-model", default=None,
        help="model name for --adjudicate (env: PERF_LINT_LLM_MODEL)",
    )
    ap.add_argument(
        "--llm-url", default=None,
        help="OpenAI-compatible base URL (env: PERF_LINT_LLM_URL, "
        "default: http://localhost:11434/v1)",
    )
    args = ap.parse_args()

    config: Config = load_config(args.paths)
    fail_on = args.fail_on or config.fail_on
    llm_model = args.llm_model or os.environ.get("PERF_LINT_LLM_MODEL") or config.llm_model
    llm_url = (
        args.llm_url or os.environ.get("PERF_LINT_LLM_URL") or config.llm_url
        or "http://localhost:11434/v1"
    )

    findings, functions = run(args.paths, config.exclude)
    title = None
    if args.diff:
        from perf_lint.gitdiff import new_findings

        findings = new_findings(
            args.diff, args.paths, findings,
            lambda paths: run(paths, config.exclude),
        )
        title = f"perf-lint: new findings vs {args.diff}:"
    suppressed = None
    if args.adjudicate:
        if not llm_model:
            ap.error("--adjudicate requires --llm-model, PERF_LINT_LLM_MODEL, "
                     "or llm_model in .perf-lint.toml")
        from perf_lint.adjudicate import LLMClient, adjudicate

        client = LLMClient(llm_url, llm_model, api_key=os.environ.get("PERF_LINT_LLM_KEY"))
        judged = adjudicate(findings, client, functions)
        for f, v in judged:
            if v.keep and v.label == "WRONG":
                f.message += f" [adjudicator disputes: {v.reason}]"
        findings = [f for f, v in judged if v.keep]
        suppressed = [(f, f"[{v.label}] {v.reason}") for f, v in judged if not v.keep]
    if args.json:
        print(render_json(findings, verbose=args.verbose))
    else:
        print(render(findings, verbose=args.verbose, suppressed=suppressed, title=title))
    sys.exit(_exit_code(findings, fail_on))
