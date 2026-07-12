"""Complexity-regression diff: current measurements vs a JSON baseline.

The check is ONE-SIDED and ambiguity-robust: a function REGRESSES only
when even its most charitable new reading (the lowest class in the new
candidate set) is strictly worse than the least charitable old one (the
highest old candidate). Anything softer — a headline drifting inside
overlapping candidate sets, a widened AMBIGUOUS set — is a WARNING, so
honest measurement noise cannot flap a CI gate. A fixed-n benchmark gate
misses exactly what this catches: a quadratic term whose coefficient is
still small at the benchmarked size.
"""
from __future__ import annotations

from typing import Any

from perfmeasure.core.model import CLASS_ORDER, FunctionReport

MEASURED_STATES = ("MEASURED", "AMBIGUOUS")


def _candidates(cls: str | None, cands: list[str]) -> list[str]:
    return cands or ([cls] if cls else [])


def _bounds(cls: str | None, cands: list[str]) -> tuple[int, int] | None:
    orders = [CLASS_ORDER[c] for c in _candidates(cls, cands)
              if c in CLASS_ORDER]
    return (min(orders), max(orders)) if orders else None


def _entry(fn: dict) -> dict:
    """Normalize one baseline record (reports_json shape)."""
    return {"time": (fn["time"].get("cls"), fn["time"].get("candidates", [])),
            "space": (fn["space"].get("cls"),
                      fn["space"].get("candidates", [])),
            "provenance": fn.get("provenance")}


def diff_reports(reports: list[FunctionReport],
                 baseline: list[dict]) -> dict[str, Any]:
    base = {fn["function"]["fid"]: _entry(fn) for fn in baseline}
    regressions, warnings, ok = [], [], 0
    seen = set()
    for r in reports:
        old = base.get(r.fid)
        seen.add(r.fid)
        if old is None:
            continue        # new function: nothing to regress against
        if r.provenance not in MEASURED_STATES:
            if old["provenance"] in MEASURED_STATES:
                warnings.append(
                    f"{r.fid}: was {old['provenance']}, now "
                    f"{r.provenance} ({r.provenance_detail})")
            continue
        for dim, new_pair in (("time", (r.time_cls, r.time_candidates)),
                              ("space", (r.space_cls, r.space_candidates))):
            old_b = _bounds(*old[dim])
            new_b = _bounds(*new_pair)
            if old_b is None or new_b is None:
                continue
            if new_b[0] > old_b[1]:
                order = sorted(CLASS_ORDER, key=CLASS_ORDER.__getitem__)
                regressions.append(
                    f"{r.fid}: {dim} {order[old_b[1]]} -> "
                    f"{order[new_b[0]]} (even the most charitable new "
                    f"reading exceeds the old worst case)")
            elif new_b[1] > old_b[1]:
                order = sorted(CLASS_ORDER, key=CLASS_ORDER.__getitem__)
                warnings.append(
                    f"{r.fid}: {dim} candidates now reach "
                    f"{order[new_b[1]]} (was <= {order[old_b[1]]}) — "
                    "overlapping sets, not a hard regression")
            else:
                ok += 1
    vanished = [fid for fid in base
                if fid not in seen and base[fid]["provenance"]
                in MEASURED_STATES]
    for fid in vanished:
        warnings.append(f"{fid}: in baseline but not measured now")
    return {"regressions": regressions, "warnings": warnings,
            "compared_ok": ok,
            "new_functions": [r.fid for r in reports if r.fid not in base]}


def render_diff(result: dict[str, Any]) -> str:
    lines = []
    for msg in result["regressions"]:
        lines.append(f"REGRESSION  {msg}")
    for msg in result["warnings"]:
        lines.append(f"warning     {msg}")
    lines.append(
        f"# {result['compared_ok']} dimension(s) within baseline, "
        f"{len(result['regressions'])} regression(s), "
        f"{len(result['warnings'])} warning(s), "
        f"{len(result['new_functions'])} new function(s)")
    return "\n".join(lines)
