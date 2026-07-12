"""Renderers: human lines and JSON. Both are projections of FunctionReport."""
from __future__ import annotations

import json
import os

from perfmeasure.core.model import (
    AMBIGUOUS, MEASURED, FunctionReport,
)


def _fname(report: FunctionReport) -> str:
    path, _, qual = report.fid.partition("::")
    if not path:
        return f"?::{qual}"
    if os.sep in path or path.endswith(".py"):
        return f"{os.path.relpath(path)}::{qual}"
    return report.fid   # crate::module::fn — not a filesystem path


def render_human(reports: list[FunctionReport], verbose: bool = False) -> str:
    lines = []
    for r in reports:
        name = _fname(r)
        if r.provenance in (MEASURED, AMBIGUOUS):
            t = r.time_cls or "?"
            if r.provenance == AMBIGUOUS:
                # headline first (it may be the ops-refined class, not the
                # worst candidate), rivals after
                rest = [c for c in r.time_candidates if c != r.time_cls]
                t = " | ".join([t] + rest)
            s = r.space_cls or "unmeasured"
            if len(r.space_candidates) > 1:
                s = " | ".join(r.space_candidates)
            worst = f" worst@{r.time_worst_shape}" if r.time_worst_shape else ""
            lines.append(f"{name}  T={t}{worst}  S={s}  "
                         f"{r.provenance} [{r.confidence}]")
        else:
            lines.append(f"{name}  {r.provenance}({r.provenance_detail})")
        if verbose:
            for shape in r.per_shape:
                pts = " ".join(f"{p.n}:{p.seconds:.2e}s" for p in shape.points)
                cls = shape.time_fit.cls if shape.time_fit else "-"
                lines.append(f"    {shape.shape:<10} {cls or '-':<12} "
                             f"stop={shape.stop_reason} {pts}")
                for f in shape.failures:
                    lines.append(f"    {shape.shape:<10} ! {f['kind']}: "
                                 f"{f['message'][:120]}")
    return "\n".join(lines)


def reports_json(reports: list[FunctionReport]) -> list[dict]:
    return [r.to_json() for r in reports]


def render_json(reports: list[FunctionReport]) -> str:
    return json.dumps(reports_json(reports), indent=2)
