"""Drive planning: which params scale, which are fixed, which shapes to sweep.

The runner already mapped each parameter's type hint to a spec tag (or
declared it undrivable with a reason). The planner decides:
  - co-scaling: all scalable params grow together with the same joint n
    (a nested loop over two lists must read O(n^2))
  - int params are scaled (as magnitude) only when nothing else scales;
    alongside collections they are held fixed at value 1
  - the shape sweep = union of shapes any scaled param supports; a scaled
    param that doesn't support the current shape falls back to "random"

M1 is hints-only: untyped params make the function UNDRIVABLE with the
runner's reason. Probing lands in M2.
"""
from __future__ import annotations

from perfmeasure.core.model import (
    SCALABLE_TAGS, TAG_SHAPES, DrivePlan, FunctionDescriptor, GenSpec,
)
from perfmeasure.protocol import seed_for

FIXED_INT_VALUE = 1


def plan(desc: FunctionDescriptor) -> tuple[DrivePlan | None, str | None]:
    """Returns (plan, None) or (None, undrivable_reason)."""
    if not desc.drivable:
        return None, desc.skip_reason or "not drivable"

    active = [p for p in desc.params if not p.omitted]
    undrivable = [p for p in active if p.spec_type is None]
    if undrivable:
        p = undrivable[0]
        return None, f"unsupported_type: param '{p.name}'" + (
            f" ({p.detail})" if p.detail else "")

    scalable = [p for p in active if p.spec_type in SCALABLE_TAGS]
    collections = [p for p in scalable if p.spec_type != "int_mag"]
    ints = [p for p in scalable if p.spec_type == "int_mag"]

    if collections:
        drivers, fixed_ints = collections, ints
    elif ints:
        drivers, fixed_ints = ints, []
    else:
        return None, "no_scalable_params"

    shapes: list[str] = []
    for p in drivers:
        for s in TAG_SHAPES[p.spec_type]:
            if s not in shapes:
                shapes.append(s)

    fixed_params = {p.name: FIXED_INT_VALUE for p in fixed_ints}
    driver_names = [p.name for p in drivers]

    def specs(shape: str, size: int) -> list[GenSpec]:
        out = []
        for p in active:
            if p in drivers:
                s = shape if shape in TAG_SHAPES[p.spec_type] else "random"
                out.append(GenSpec(p.spec_type, s, size,
                                   seed_for(desc.fid, s, size)))
            else:  # fixed int
                out.append(GenSpec("int_mag", "magnitude", FIXED_INT_VALUE,
                                   seed_for(desc.fid, "fixed", 0)))
        return out

    return DrivePlan(driver_params=driver_names, fixed_params=fixed_params,
                     shapes=shapes, specs=specs), None
