"""Renderers: human lines and JSON. Both are projections of FunctionReport."""
from __future__ import annotations

import json
import os

from perfmeasure.core.model import (
    AMBIGUOUS, MEASURED, FunctionReport,
)


def _fname(report: FunctionReport) -> str:
    path, _, qual = report.fid.partition("::")
    return f"{os.path.relpath(path) if path else '?'}::{qual}"


def render_human(reports: list[FunctionReport], verbose: bool = False) -> str:
    lines = []
    for r in reports:
        name = _fname(r)
        if r.provenance in (MEASURED, AMBIGUOUS):
            t = r.time_cls or "?"
            if r.provenance == AMBIGUOUS:
                t = " | ".join(r.time_candidates)
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


def render_json(reports: list[FunctionReport]) -> str:
    return json.dumps([r.to_json() for r in reports], indent=2)
