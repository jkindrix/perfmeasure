"""Accuracy eval: run the tool against the ground-truth corpus and score it.

Usage: python evals/harness.py [--budget S] [--only QUALNAME] [--verbose]

Scoring per function:
  - expected UNDRIVABLE: pass iff the tool says UNDRIVABLE
  - otherwise: time passes iff headline == expected OR expected is in the
    AMBIGUOUS candidate set ("ambiguous-contains-truth"); space (when the
    corpus pins it) is scored the same way; "worst_shape_not" asserts the
    worst shape is not the named one (insertion sort must not look worst
    on sorted input).
Reported: exact-time rate, pass rate (incl. ambiguous-contains-truth),
space pass rate, UNDRIVABLE precision. Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perfmeasure.core.ladder import Budget                     # noqa: E402
from perfmeasure.core.report import render_human               # noqa: E402
from perfmeasure.cli import measure_target                     # noqa: E402

CORPUS_FILES = sorted((Path(__file__).parent / "corpus").glob("*.py"))
EXPECTED = json.loads((Path(__file__).parent / "expected.json").read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=8.0)
    parser.add_argument("--only")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    t0 = time.time()
    reports, interp = [], "?"
    for corpus in CORPUS_FILES:
        try:
            rs, interp = measure_target(
                str(corpus), args.only, Budget(per_function_s=args.budget))
        except RuntimeError as e:
            if args.only and "not found" in str(e):
                continue
            raise
        reports.extend(rs)
    print(f"# interpreter: {interp}")
    by_name = {r.fid.rpartition("::")[2]: r for r in reports}

    rows, failures = [], []
    exact = ambig_ok = time_total = 0
    space_ok = space_total = 0
    undrv_ok = undrv_total = 0
    for name, exp in EXPECTED.items():
        if args.only and name != args.only:
            continue
        r = by_name.get(name)
        if r is None:
            failures.append(f"{name}: not measured at all")
            rows.append((name, "MISSING", "", "FAIL"))
            continue
        if exp.get("provenance") == "UNDRIVABLE":
            undrv_total += 1
            ok = r.provenance == "UNDRIVABLE"
            undrv_ok += ok
            rows.append((name, r.provenance, r.provenance_detail or "",
                         "ok" if ok else "FAIL"))
            if not ok:
                failures.append(f"{name}: expected UNDRIVABLE, got "
                                f"{r.provenance} T={r.time_cls}")
            continue

        time_total += 1
        verdicts = []
        accepted = exp.get("time_any", [exp["time"]])
        t_exact = r.time_cls == exp["time"]
        t_ok = (t_exact or r.time_cls in accepted
                or exp["time"] in r.time_candidates)
        exact += t_exact
        ambig_ok += t_ok
        verdicts.append("ok" if t_ok else "FAIL")
        if not t_ok:
            failures.append(
                f"{name}: time expected {exp['time']}, got {r.time_cls} "
                f"(candidates {r.time_candidates}, {r.provenance}"
                f"{': ' + (r.provenance_detail or '') if r.provenance_detail else ''})")
        if exp.get("space"):
            space_total += 1
            s_ok = r.space_cls == exp["space"] or exp["space"] in r.space_candidates
            space_ok += s_ok
            verdicts.append("ok" if s_ok else "FAIL")
            if not s_ok:
                failures.append(f"{name}: space expected {exp['space']}, "
                                f"got {r.space_cls} ({r.space_candidates})")
        if exp.get("worst_shape_not") and r.time_worst_shape == exp["worst_shape_not"]:
            verdicts.append("FAIL")
            failures.append(f"{name}: worst shape must not be "
                            f"{exp['worst_shape_not']}")
        if exp.get("expect_flag") and not r.flags.get(exp["expect_flag"]):
            verdicts.append("FAIL")
            failures.append(f"{name}: flag {exp['expect_flag']} not set "
                            f"(flags: {r.flags})")
        for pname, how in exp.get("expect_source", {}).items():
            if r.type_source.get(pname) != how:
                verdicts.append("FAIL")
                failures.append(f"{name}: param {pname} type_source expected "
                                f"{how}, got {r.type_source.get(pname)}")
        for pname, val in exp.get("expect_fixed", {}).items():
            if r.fixed_params.get(pname) != val:
                verdicts.append("FAIL")
                failures.append(f"{name}: fixed param {pname} expected "
                                f"{val!r}, got {r.fixed_params.get(pname)!r}")
        shown = " | ".join(r.time_candidates) if len(r.time_candidates) > 1 \
            else (r.time_cls or r.provenance)
        rows.append((name, shown, r.space_cls or "-",
                     "ok" if all(v == "ok" for v in verdicts) else "FAIL"))
        if args.verbose:
            print(render_human([r], verbose=True))

    width = max(len(n) for n, *_ in rows)
    for name, t, s, verdict in rows:
        print(f"{name:<{width}}  T={t:<24} S={s:<10} {verdict}")
    print(f"\ntime:  {ambig_ok}/{time_total} pass "
          f"({exact}/{time_total} exact, rest ambiguous-contains-truth)")
    if space_total:
        print(f"space: {space_ok}/{space_total} pass")
    if undrv_total:
        print(f"undrivable precision: {undrv_ok}/{undrv_total}")
    print(f"wall: {time.time() - t0:.0f}s")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
