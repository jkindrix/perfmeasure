"""Wild corpus: drivability on REAL projects, tracked as a regression metric.

The synthetic corpus (harness.py) scores accuracy; this scores *contact
with reality* — what fraction of real public functions the tool can
measure, and why the rest can't. All three of the first field-found issue
classes (feature-gated modules, missing whitelist types, denominator
noise) were invisible on synthetic code and obvious here.

Usage:
  python evals/wild.py            # scan targets, compare to wild-baseline.json
  python evals/wild.py --update   # rewrite the baseline to current results

Regression = a target's measured count dropping below baseline. New
undrivable reasons are reported (they're the next whitelist/feature work).
Targets live in wild.json; missing paths are skipped with a note so the
file stays portable across machines.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perfmeasure.core.ladder import Budget                     # noqa: E402
from perfmeasure.cli import measure_target, scan_target        # noqa: E402

HERE = Path(__file__).parent
TARGETS = json.loads((HERE / "wild.json").read_text())
BASELINE = HERE / "wild-baseline.json"


def scan_one(target: dict, budget: float) -> dict | None:
    path = Path(target["path"]).expanduser()
    if not path.is_absolute():
        path = HERE.parent / path
    if not path.exists():
        print(f"# skipped (missing): {target['path']}", file=sys.stderr)
        return None
    if (path / "Cargo.toml").exists():
        reports, _ = measure_target(str(path), None,
                                    Budget(per_function_s=budget),
                                    features=target.get("features"))
    else:
        reports, _, _ = scan_target(str(path), Budget(per_function_s=budget))
    measured = sum(r.provenance in ("MEASURED", "AMBIGUOUS") for r in reports)
    reasons = Counter((r.provenance_detail or "").split(":")[0]
                      for r in reports if r.provenance == "UNDRIVABLE")
    return {"functions": len(reports), "measured": measured,
            "reasons": dict(reasons)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--budget", type=float, default=3.0)
    args = parser.parse_args()

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    results: dict[str, dict] = {}
    failures = []
    for target in TARGETS:
        r = scan_one(target, args.budget)
        if r is None:
            continue
        name = target["path"]
        results[name] = r
        base = baseline.get(name)
        ratio = f"{r['measured']}/{r['functions']}"
        print(f"{name}: {ratio} measured; reasons: "
              + (", ".join(f"{k or 'other'}: {v}"
                           for k, v in sorted(r["reasons"].items(),
                                              key=lambda kv: -kv[1]))
                 or "none"))
        if base:
            if r["measured"] < base["measured"]:
                failures.append(f"{name}: measured {r['measured']} < "
                                f"baseline {base['measured']}")
            new_reasons = set(r["reasons"]) - set(base["reasons"])
            if new_reasons:
                print(f"  new undrivable reasons vs baseline: "
                      f"{sorted(new_reasons)} — candidate whitelist/feature work")

    if args.update:
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"baseline updated: {BASELINE}")
        return 0
    if failures:
        print("\nDRIVABILITY REGRESSIONS:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
