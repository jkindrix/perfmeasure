"""Accuracy eval: run the tool against the ground-truth corpus and score it.

Usage: python evals/harness.py [--budget S] [--only QUALNAME] [--verbose]

Scoring per function:
  - expected UNDRIVABLE: pass iff the tool says UNDRIVABLE. Recall is
    split by undrivable_kind: "inherent" (no meaningful measurement
    exists) vs "tool_limit" (our whitelist/generics gap) — scoring the
    tool for its own limitations must be visible, not recall padding.
  - otherwise: time passes iff headline == expected OR expected is in the
    AMBIGUOUS candidate set ("ambiguous-contains-truth"); space (when the
    corpus pins it) is scored the same way; "worst_shape_not" asserts the
    worst shape is not the named one (insertion sort must not look worst
    on sorted input).

Gate teeth (each failure is a hard exit 1):
  - per-case candidate width <= max_width (default 2): truth-in-a-stuffed-
    set is not a pass. Cases that legitimately need more get an explicit
    max_width in expected.json.
  - mean width <= MEAN_WIDTH_CEILING: wholesale widening cannot hide.
  - exact count >= EXACT_FLOOR: regressions cannot hide inside adjacency.
  - passes that exist ONLY via time_any adjacency <= ADJACENT_ONLY_MAX
    (a ratchet: tighten it when the fitter improves, never loosen).
  - every measured corpus function must have an expected entry: wrong
    answers on unlisted functions are not free.
Reported: exact-time rate (per class too), pass rate, space pass rate,
split UNDRIVABLE recall. Exit 1 on any failure.
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

import shutil

CORPUS_FILES = sorted((Path(__file__).parent / "corpus").glob("*.py"))
RUST_CORPUS = Path(__file__).parent / "corpus_rust"

DEFAULT_MAX_WIDTH = 2       # per-case candidate-set ceiling (E: anti-stuffing)
MEAN_WIDTH_CEILING = 1.75   # global mean width ceiling
EXACT_FLOOR = 72            # ratchet upward when the fitter improves
                            # (80 observed with the instructions channel)
ADJACENT_ONLY_MAX = 4       # passes that exist only via time_any adjacency
                            # (2 observed with the instructions channel)
SPACE_ADJACENT_ONLY_MAX = 2  # same ratchet for space_any: space-side
                             # inflation must not hide inside adjacency
                             # (0 observed with the coefficient-step check)
EXPECTED = json.loads((Path(__file__).parent / "expected.json").read_text())
if shutil.which("cargo"):
    EXPECTED.update(json.loads(
        (Path(__file__).parent / "expected-rust.json").read_text()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=float, default=4.0)
    parser.add_argument("--only")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--update-readme", action="store_true",
                        help="write the accuracy paragraph into README.md "
                             "between the gate markers (kills figure drift)")
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
    if shutil.which("cargo") and not args.only:
        rs, _ = measure_target(str(RUST_CORPUS), None,
                               Budget(per_function_s=args.budget))
        reports.extend(rs)
    elif not shutil.which("cargo"):
        print("# cargo not found: Rust corpus skipped", file=sys.stderr)
    print(f"# interpreter: {interp}")

    def find_report(key):
        """An expected key must identify exactly ONE report — suffix-matched
        so keys stay readable; same-named fns disambiguate with a longer
        key (e.g. "RsOpts::new"). Every measured corpus fn must be
        scored, so ambiguity fails loud here and coverage fails below."""
        matches = [r for r in reports
                   if r.fid == key or r.fid.endswith("::" + key)
                   or r.fid.endswith("." + key)]
        if len(matches) > 1:
            raise SystemExit(
                f"error: expected key {key!r} matches multiple reports: "
                + ", ".join(m.fid for m in matches))
        return matches[0] if matches else None

    rows, failures, widths = [], [], []
    exact = ambig_ok = time_total = adjacent_only = 0
    space_ok = space_total = space_adjacent_only = 0
    undrv = {"inherent": [0, 0], "tool_limit": [0, 0]}
    by_class: dict[str, list[int]] = {}
    scored_fids: set[str] = set()
    for name, exp in EXPECTED.items():
        if args.only and name != args.only:
            continue
        r = find_report(name)
        if r is None:
            failures.append(f"{name}: not measured at all")
            rows.append((name, "MISSING", "", "FAIL"))
            continue
        scored_fids.add(r.fid)
        if exp.get("provenance") == "UNDRIVABLE":
            kind = exp.get("undrivable_kind", "inherent")
            undrv[kind][1] += 1
            ok = r.provenance == "UNDRIVABLE" and (
                exp.get("detail_contains", "") in (r.provenance_detail or ""))
            undrv[kind][0] += ok
            rows.append((name, r.provenance, r.provenance_detail or "",
                         "ok" if ok else "FAIL"))
            if not ok:
                failures.append(f"{name}: expected UNDRIVABLE, got "
                                f"{r.provenance} T={r.time_cls}")
            continue

        time_total += 1
        verdicts = []
        width = max(1, len(r.time_candidates))
        widths.append(width)
        max_width = exp.get("max_width", DEFAULT_MAX_WIDTH)
        if width > max_width:
            verdicts.append("FAIL")
            failures.append(
                f"{name}: candidate width {width} > {max_width} "
                f"({r.time_candidates}) — truth in a stuffed set is not a pass")
        accepted = exp.get("time_any", [exp["time"]])
        t_exact = r.time_cls == exp["time"]
        t_ok = (t_exact or r.time_cls in accepted
                or exp["time"] in r.time_candidates)
        if t_ok and not t_exact and exp["time"] not in r.time_candidates:
            adjacent_only += 1
        exact += t_exact
        ambig_ok += t_ok
        cls_stat = by_class.setdefault(exp["time"], [0, 0])
        cls_stat[0] += t_exact
        cls_stat[1] += 1
        verdicts.append("ok" if t_ok else "FAIL")
        if not t_ok:
            failures.append(
                f"{name}: time expected {exp['time']}, got {r.time_cls} "
                f"(candidates {r.time_candidates}, {r.provenance}"
                f"{': ' + (r.provenance_detail or '') if r.provenance_detail else ''})")
        if exp.get("space"):
            space_total += 1
            s_accepted = exp.get("space_any", [exp["space"]])
            s_ok = (r.space_cls in s_accepted
                    or exp["space"] in r.space_candidates)
            space_ok += s_ok
            if (s_ok and r.space_cls != exp["space"]
                    and exp["space"] not in r.space_candidates):
                space_adjacent_only += 1
            verdicts.append("ok" if s_ok else "FAIL")
            if not s_ok:
                failures.append(f"{name}: space expected {exp['space']}, "
                                f"got {r.space_cls} ({r.space_candidates})")
        if exp.get("worst_shape_not") and r.time_worst_shape == exp["worst_shape_not"]:
            verdicts.append("FAIL")
            failures.append(f"{name}: worst shape must not be "
                            f"{exp['worst_shape_not']}")
        if exp.get("worst_shape") and r.time_worst_shape != exp["worst_shape"]:
            verdicts.append("FAIL")
            failures.append(f"{name}: worst shape expected "
                            f"{exp['worst_shape']}, got {r.time_worst_shape}")
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

    # E5: every measured corpus function must be in the expected files —
    # wrong answers on unlisted functions must not be free
    if not args.only:
        unscored = sorted(r.fid for r in reports if r.fid not in scored_fids)
        for fid in unscored:
            failures.append(f"unscored corpus function (add an expected "
                            f"entry or remove it): {fid}")

    colw = max(len(n) for n, *_ in rows)
    for name, t, s, verdict in rows:
        print(f"{name:<{colw}}  T={t:<24} S={s:<10} {verdict}")
    mean_width = sum(widths) / max(1, len(widths))
    print(f"\ntime:  {ambig_ok}/{time_total} pass "
          f"({exact}/{time_total} exact, rest ambiguous-contains-truth; "
          f"mean ambiguity width {mean_width:.2f}; "
          f"{adjacent_only} pass only via adjacency)")
    print("exact by class: " + ", ".join(
        f"{cls} {ok}/{total}"
        for cls, (ok, total) in sorted(by_class.items())))
    if space_total:
        print(f"space: {space_ok}/{space_total} pass "
              f"({space_adjacent_only} pass only via adjacency)")
    undrv_ok = sum(v[0] for v in undrv.values())
    undrv_total = sum(v[1] for v in undrv.values())
    if undrv_total:
        print(f"undrivable recall: {undrv_ok}/{undrv_total} "
              f"(inherent {undrv['inherent'][0]}/{undrv['inherent'][1]}, "
              f"tool-limit {undrv['tool_limit'][0]}/{undrv['tool_limit'][1]})")
    wall = time.time() - t0
    print(f"wall: {wall:.0f}s")

    if not args.only:
        if mean_width > MEAN_WIDTH_CEILING:
            failures.append(f"mean ambiguity width {mean_width:.2f} > "
                            f"ceiling {MEAN_WIDTH_CEILING}")
        if exact < EXACT_FLOOR:
            failures.append(f"exact count {exact} < floor {EXACT_FLOOR} — "
                            "a regression is hiding inside adjacency")
        if adjacent_only > ADJACENT_ONLY_MAX:
            failures.append(f"{adjacent_only} adjacency-only passes > "
                            f"ratchet {ADJACENT_ONLY_MAX}")
        if space_adjacent_only > SPACE_ADJACENT_ONLY_MAX:
            failures.append(f"{space_adjacent_only} space adjacency-only "
                            f"passes > ratchet {SPACE_ADJACENT_ONLY_MAX}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    if args.update_readme:
        _update_readme(ambig_ok, time_total, exact, mean_width,
                       space_ok, space_total, undrv_ok, undrv_total, wall)
    return 0


def _update_readme(t_ok, t_total, exact, width, s_ok, s_total,
                   u_ok, u_total, wall, readme: Path | None = None):
    """README figures are GENERATED, never hand-written — hand-written
    ones went stale twice."""
    para = (f"Current run: **{t_ok}/{t_total} time classes** ({exact} exact, "
            f"rest ambiguous-containing-truth, mean ambiguity width "
            f"{width:.2f}), **{s_ok}/{s_total} space classes**, "
            f"**{u_ok}/{u_total} undrivable recall** — full gate in "
            f"~{wall:.0f} s.")
    readme = readme or Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text()
    if "<!-- gate:begin" not in text or "<!-- gate:end -->" not in text:
        raise SystemExit(f"{readme}: gate markers missing — cannot splice "
                         "the accuracy paragraph")
    begin = text.index("<!-- gate:begin")
    begin = text.index("\n", begin) + 1
    end = text.index("<!-- gate:end -->")
    readme.write_text(text[:begin] + para + "\n" + text[end:])
    print(f"README gate figures updated: {para}")


if __name__ == "__main__":
    sys.exit(main())
