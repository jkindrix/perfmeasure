from __future__ import annotations

import argparse
import os
import sys

from perf_lint.adapters import ADAPTERS
from perf_lint.analysis import UNKNOWN, Finding, analyze_function, build_summaries
from perf_lint.costs import load_costs
from perf_lint.report import render, render_json

SKIP_DIRS = {"__pycache__"}
LANGUAGES = {".py": "python"}


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


def run(paths: list[str]) -> tuple[list[Finding], list]:
    functions = []
    for path in collect_files(paths):
        adapter = next(a for a in ADAPTERS if path.endswith(a.extensions))
        with open(path, "rb") as f:
            source = f.read()
        functions.extend(adapter.parse(path, source))
    costs = load_costs("python")
    summaries = build_summaries(functions, costs)
    findings: list[Finding] = []
    for fn in functions:
        findings.extend(analyze_function(fn, costs, summaries))
    return findings, functions


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
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--adjudicate", action="store_true",
        help="review findings with an LLM and suppress benign/wrong ones "
        "(suppress-only; fails open)",
    )
    ap.add_argument(
        "--llm-model",
        default=os.environ.get("PERF_LINT_LLM_MODEL"),
        help="model name for --adjudicate (env: PERF_LINT_LLM_MODEL)",
    )
    ap.add_argument(
        "--llm-url",
        default=os.environ.get("PERF_LINT_LLM_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible base URL (env: PERF_LINT_LLM_URL)",
    )
    args = ap.parse_args()

    findings, functions = run(args.paths)
    suppressed = None
    if args.adjudicate:
        if not args.llm_model:
            ap.error("--adjudicate requires --llm-model or PERF_LINT_LLM_MODEL")
        from perf_lint.adjudicate import LLMClient, adjudicate

        client = LLMClient(
            args.llm_url, args.llm_model,
            api_key=os.environ.get("PERF_LINT_LLM_KEY"),
        )
        judged = adjudicate(findings, client, functions)
        for f, v in judged:
            if v.keep and v.label == "WRONG":
                f.message += f" [adjudicator disputes: {v.reason}]"
        findings = [f for f, v in judged if v.keep]
        suppressed = [(f, f"[{v.label}] {v.reason}") for f, v in judged if not v.keep]
    if args.json:
        print(render_json(findings, verbose=args.verbose))
    else:
        print(render(findings, verbose=args.verbose, suppressed=suppressed))
    sys.exit(1 if any(f.severity != UNKNOWN for f in findings) else 0)
