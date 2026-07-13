"""Held-out accuracy check: score the tool against evals/heldout/, a
corpus whose labels are cited from external documentation and whose
curves the fitter's thresholds have never been calibrated against.

Usage:
  python evals/heldout.py --smoke     # provenance-only drivability check
                                      # (prints NO classes: authoring bugs
                                      # can be fixed while staying blind)
  python evals/heldout.py             # the scored run — report verbatim

This is EVIDENCE, not a gate: there are no ratchets to tune, and the
numbers are reported as they come out. Scoring mirrors the calibration
gate (pass = exact headline, or truth in the AMBIGUOUS candidates, or a
pre-registered time_any adjacency; candidate width capped at 2). The
sealing rules live in evals/heldout/corpus_heldout.py's docstring:
consulting a case for a tuning decision retires it into the calibration
corpus.

Exit 1 only for protocol violations (a corpus function without a label,
a label without a measurement) — never for accuracy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perfmeasure.core.ladder import Budget                     # noqa: E402
from perfmeasure.cli import measure_target                     # noqa: E402

HERE = Path(__file__).parent
CORPUS = HERE / "heldout" / "corpus_heldout.py"
EXPECTED = {k: v for k, v in json.loads(
    (HERE / "heldout" / "expected-heldout.json").read_text()).items()
    if not k.startswith("_")}
MAX_WIDTH = 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=4.0)
    parser.add_argument("--smoke", action="store_true",
                        help="drivability only; prints no classes")
    args = parser.parse_args()

    t0 = time.time()
    reports, _ = measure_target(str(CORPUS), None,
                                Budget(per_function_s=args.budget))
    by_name = {r.fid.rpartition("::")[2]: r for r in reports}

    if args.smoke:
        bad = 0
        for name in sorted(EXPECTED):
            r = by_name.get(name)
            prov = r.provenance if r else "MISSING"
            ok = prov in ("MEASURED", "AMBIGUOUS")
            bad += not ok
            detail = f" ({r.provenance_detail})" if r is not None \
                and not ok and r.provenance_detail else ""
            print(f"{name:<28} {prov}{detail}")
        extra = sorted(set(by_name) - set(EXPECTED))
        for name in extra:
            print(f"{name:<28} UNLABELED corpus function")
        print(f"# smoke: {len(EXPECTED) - bad}/{len(EXPECTED)} drivable, "
              f"{len(extra)} unlabeled")
        return 1 if bad or extra else 0

    rows, protocol_failures, widths = [], [], []
    exact = passed = adjacent_only = width_violations = 0
    space_ok = space_total = 0
    by_class: dict[str, list[int]] = {}
    for name, exp in EXPECTED.items():
        r = by_name.get(name)
        if r is None or r.provenance not in ("MEASURED", "AMBIGUOUS"):
            prov = r.provenance if r else "MISSING"
            rows.append((name, prov, "-", "FAIL"))
            protocol_failures.append(
                f"{name}: expected a measurement, got {prov}"
                + (f" ({r.provenance_detail})"
                   if r and r.provenance_detail else ""))
            continue
        width = max(1, len(r.time_candidates))
        widths.append(width)
        if width > MAX_WIDTH:
            width_violations += 1
        accepted = exp.get("time_any", [exp["time"]])
        t_exact = r.time_cls == exp["time"]
        t_ok = (t_exact or r.time_cls in accepted
                or exp["time"] in r.time_candidates)
        if t_ok and not t_exact and exp["time"] not in r.time_candidates:
            adjacent_only += 1
        exact += t_exact
        passed += t_ok
        cls_stat = by_class.setdefault(exp["time"], [0, 0])
        cls_stat[0] += t_exact
        cls_stat[1] += 1
        verdict = "ok" if t_ok and width <= MAX_WIDTH else "MISS"
        if exp.get("space"):
            space_total += 1
            s_ok = (r.space_cls == exp["space"]
                    or exp["space"] in r.space_candidates)
            space_ok += s_ok
            if not s_ok:
                verdict = "MISS"
        shown = " | ".join(r.time_candidates) if len(r.time_candidates) > 1 \
            else (r.time_cls or "?")
        rows.append((name, shown, r.space_cls or "-", verdict))

    for name in sorted(set(by_name) - set(EXPECTED)):
        protocol_failures.append(f"unlabeled corpus function: {name}")

    colw = max(len(n) for n, *_ in rows)
    for name, t, s, verdict in rows:
        print(f"{name:<{colw}}  T={t:<24} S={s:<10} {verdict}")
    total = len(EXPECTED) - len(protocol_failures)
    mean_width = sum(widths) / max(1, len(widths))
    print(f"\nheld-out time:  {passed}/{total} pass ({exact}/{total} exact, "
          f"rest ambiguous-contains-truth; mean width {mean_width:.2f}; "
          f"{adjacent_only} pass only via pre-registered adjacency; "
          f"{width_violations} width violation(s))")
    print("exact by class: " + ", ".join(
        f"{cls} {ok}/{n}" for cls, (ok, n) in sorted(by_class.items())))
    if space_total:
        print(f"held-out space: {space_ok}/{space_total} pass")
    print(f"wall: {time.time() - t0:.0f}s")
    if protocol_failures:
        print("\nPROTOCOL FAILURES:")
        for f in protocol_failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
