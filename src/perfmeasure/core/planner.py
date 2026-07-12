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
FIXED_VARIANTS = 3      # 0: value 1, 1: value 0, 2: half of the first driver
# drivable but never scalable; wire value = GenSpec.size
FIXED_TAGS = {"bool_": 0, "duration_ms": 1}
FIXED_TAG_DISPLAY = {"bool_": False, "duration_ms": "1ms"}


def plan(desc: FunctionDescriptor, fixed_variant: int = 0
         ) -> tuple[DrivePlan | None, str | None]:
    """Returns (plan, None) or (None, undrivable_reason). fixed_variant
    selects the fallback strategy for held-fixed int params — the
    orchestrator walks variants when the first ladder call is rejected."""
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
    # non-scalable-but-drivable tags: held at a fixed value, never scaled
    # (a Duration is a timeout — scaling it would measure the sleep)
    fixed_other = [p for p in active if p.spec_type in FIXED_TAGS]

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

    driver_names = [p.name for p in drivers]
    first_driver_idx = next(i for i, p in enumerate(active) if p in drivers)
    if fixed_variant == 0:
        fixed_desc = 1
    elif fixed_variant == 1:
        fixed_desc = 0
    else:
        fixed_desc = f"half_of:{driver_names[0]}"
    fixed_params = {p.name: fixed_desc for p in fixed_ints}
    fixed_params.update(
        {p.name: FIXED_TAG_DISPLAY[p.spec_type] for p in fixed_other})

    def specs(shape: str, size: int) -> list[GenSpec]:
        out = []
        for p in active:
            if p in drivers:
                s = shape if shape in TAG_SHAPES[p.spec_type] else "random"
                out.append(GenSpec(p.spec_type, s, size,
                                   seed_for(desc.fid, s, size)))
            elif p.spec_type in FIXED_TAGS:
                out.append(GenSpec(p.spec_type, "fixed",
                                   FIXED_TAGS[p.spec_type],
                                   seed_for(desc.fid, "fixed", 0)))
            elif fixed_variant == 2:
                spec = GenSpec("int_half_of", "magnitude", 0,
                               seed_for(desc.fid, "fixed", 0))
                spec.of_index = first_driver_idx
                out.append(spec)
            else:
                out.append(GenSpec("int_mag", "magnitude", fixed_desc,
                                   seed_for(desc.fid, "fixed", 0)))
        return out

    return DrivePlan(driver_params=driver_names, fixed_params=fixed_params,
                     shapes=shapes, specs=specs), None
