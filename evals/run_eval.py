"""Benchmark adjudication models against hand-labeled findings.

Usage: uv run python evals/run_eval.py MODEL [MODEL...] [--url URL]

For each model: adjudicate every labeled finding, report label agreement and
the metric that matters — keep/suppress accuracy (ACTIONABLE = keep,
BENIGN/WRONG = suppress).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from perf_lint.adjudicate import LLMClient, adjudicate  # noqa: E402
from perf_lint.cli import run  # noqa: E402


def load_labeled_findings(labels_path):
    with open(labels_path) as f:
        spec = json.load(f)
    repos = [os.path.expanduser(r) for r in spec["repos"]]
    findings, functions = run(repos)
    labeled = []
    seen = set()
    for label in spec["labels"]:
        key = (label["file"], label["line"], label["complexity"])
        if key in seen:
            continue  # duplicate label (two findings can share line+complexity)
        seen.add(key)
        match = [
            f for f in findings
            if f.file.endswith(label["file"])
            and f.line == label["line"]
            and f.complexity == label["complexity"]
        ]
        if not match:
            print(f"!! label matched no findings: {label}")
            continue
        labeled.extend((m, label["expected"]) for m in match)
    return labeled, functions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--url", default="http://localhost:11434/v1")
    ap.add_argument(
        "--labels",
        default=os.path.join(os.path.dirname(__file__), "labels.json"),
        help="labels file (e.g. evals/labels-rust.json)",
    )
    args = ap.parse_args()

    labeled, functions = load_labeled_findings(args.labels)
    print(f"{len(labeled)} labeled findings\n")

    for model in args.models:
        client = LLMClient(args.url, model, api_key=os.environ.get("PERF_LINT_LLM_KEY"))
        t0 = time.time()
        judged = adjudicate([f for f, _ in labeled], client, functions)
        elapsed = time.time() - t0

        label_hits = unadjudicated = noise = noise_kept = 0
        false_suppress = []  # worst error: a real finding silenced
        for (finding, expected), (_, verdict) in zip(labeled, judged):
            expected_keep = expected == "ACTIONABLE"
            if verdict.label == "UNADJUDICATED":
                unadjudicated += 1
            if verdict.label == expected:
                label_hits += 1
            if not expected_keep:
                noise += 1
                if verdict.keep:
                    noise_kept += 1
            elif not verdict.keep:
                false_suppress.append((finding, verdict))
            mark = "ok " if verdict.keep == expected_keep else "MISS"
            loc = f"{os.path.basename(finding.file)}:{finding.line}"
            print(f"  [{mark}] {loc:38} expected={expected:10} got={verdict.label}")

        n = len(labeled)
        print(f"\n{model}:")
        print(f"  false suppressions:     {len(false_suppress)}"
              + "".join(f"\n    - {f.file}:{f.line} ({v.reason})" for f, v in false_suppress))
        print(f"  noise suppressed:       {noise - noise_kept}/{noise}")
        print(f"  exact label agreement:  {label_hits}/{n} ({100 * label_hits // n}%)")
        print(f"  unadjudicated:          {unadjudicated}")
        print(f"  time: {elapsed:.0f}s ({elapsed / n:.1f}s/finding)\n")


if __name__ == "__main__":
    main()
