"""Wild corpus: drivability on REAL projects, tracked as a regression metric.

The synthetic corpus (harness.py) scores accuracy; this scores *contact
with reality* — what fraction of real public functions the tool can
measure, and why the rest can't. All three of the first field-found issue
classes (feature-gated modules, missing whitelist types, denominator
noise) were invisible on synthetic code and obvious here.

Usage:
  python evals/wild.py            # scan targets, compare to wild-baseline.json
  python evals/wild.py --update   # rewrite the baseline to current results

Regression = a target's STRUCTURALLY drivable count (measured plus
budget-bound: TIMEOUT / insufficient_range) dropping below baseline.
Structural drivability — types supported, planning, discovery — is
deterministic; the measured count alone also moves with machine timing,
because borderline ladders flip between measured and insufficient_range
with load, so its drift is reported but never fails the gate. New
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
    # budget-bound outcomes were DRIVEN (inputs generated, calls ran) but
    # the ladder ran out of budget or points — a timing fact, not a lost
    # capability; they count toward structural drivability
    budget_bound = sum(
        r.provenance == "TIMEOUT"
        or (r.provenance == "UNDRIVABLE"
            and (r.provenance_detail or "").startswith("insufficient_range"))
        for r in reports)
    reasons = Counter(
        (r.provenance_detail or "").split(":")[0]
        for r in reports
        if r.provenance == "UNDRIVABLE"
        and not (r.provenance_detail or "").startswith("insufficient_range"))
    return {"functions": len(reports), "measured": measured,
            "structural": measured + budget_bound,
            "budget_bound": budget_bound,
            "reasons": dict(reasons)}


def regressions(name: str, result: dict, base: dict | None) -> list[str]:
    """Drivability regressions for one target vs its baseline: a drop in
    the STRUCTURAL count (measured + budget-bound) fails — that count is
    machine-deterministic. A measured-only drop is timing, not loss, and
    is visible in the per-target line instead. New undrivable reasons
    are reported (they are the next whitelist/feature work) but do not
    fail. (Baselines predating the structural field fall back to their
    measured count.)"""
    if not base:
        return []
    out = []
    base_structural = base.get("structural", base["measured"])
    if result["structural"] < base_structural:
        out.append(f"{name}: structurally drivable {result['structural']} < "
                   f"baseline {base_structural}")
    return out


def new_reasons(result: dict, base: dict | None) -> list[str]:
    if not base:
        return []
    return sorted(set(result["reasons"]) - set(base["reasons"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--budget", type=float, default=3.0)
    args = parser.parse_args()

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    results: dict[str, dict] = {}
    failures = []
    skipped = 0
    for target in TARGETS:
        r = scan_one(target, args.budget)
        if r is None:
            skipped += 1
            continue
        name = target["path"]
        results[name] = r
        base = baseline.get(name)
        ratio = f"{r['measured']}/{r['functions']}"
        bb = (f" (+{r['budget_bound']} budget-bound)"
              if r["budget_bound"] else "")
        print(f"{name}: {ratio} measured{bb}; reasons: "
              + (", ".join(f"{k or 'other'}: {v}"
                           for k, v in sorted(r["reasons"].items(),
                                              key=lambda kv: -kv[1]))
                 or "none"))
        failures.extend(regressions(name, r, base))
        fresh = new_reasons(r, base)
        if fresh:
            print(f"  new undrivable reasons vs baseline: "
                  f"{fresh} — candidate whitelist/feature work")

    # a skipped target is a hole in the gate, not a pass: say so on stdout,
    # and zero checked targets is a failure — a vacuous green is the one
    # result this gate must never produce
    print(f"# wild gate: {len(results)}/{len(TARGETS)} targets present"
          + (f" ({skipped} missing on this machine — gate is PARTIAL)"
             if skipped else ""))
    if not results:
        print("error: no wild targets exist on this machine — "
              "nothing was checked", file=sys.stderr)
        return 1

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
