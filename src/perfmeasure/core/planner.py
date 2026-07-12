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
# scalar magnitudes (int/float): scaled only when nothing else scales
MAGNITUDE_TAGS = {"int_mag", "float_mag"}
# drivable but never scalable; wire value = GenSpec.size
FIXED_TAGS = {"bool_": 0, "duration_ms": 1, "opt_none": 0, "instance_": 0}
FIXED_TAG_DISPLAY = {"bool_": False, "duration_ms": "1ms", "opt_none": None}


def _opt_flippable(desc: FunctionDescriptor) -> bool:
    return any(p.spec_type == "opt_none" and p.type_ref
               for p in desc.params if not p.omitted)


def variants(desc: FunctionDescriptor) -> list[tuple[int, bool]]:
    """The meaningful (fixed_variant, opt_some) fallback combinations the
    orchestrator may walk on first-size rejection. Only functions that
    actually hold ints fixed get the int strategies, and only functions
    with a synthesizable Some get the Option flip — anything else would
    replay identical inputs for an identical rejection.

    Mirrors plan()'s driver selection: ints are held fixed only when a
    collection param is the driver."""
    active = [p for p in desc.params if not p.omitted]
    scalable = [p for p in active if p.spec_type in SCALABLE_TAGS]
    coll = any(p.spec_type not in MAGNITUDE_TAGS for p in scalable)
    ints = any(p.spec_type in MAGNITUDE_TAGS for p in scalable)
    recv = bool(desc.receiver and desc.receiver_fill)
    # the half-of variant needs a param driver to halve against, so a
    # receiver-only drive walks just the 1/0 int strategies
    if ints and coll:
        ivs = range(FIXED_VARIANTS)
    elif ints and recv:
        ivs = range(2)
    else:
        ivs = (0,)
    opts = (False, True) if _opt_flippable(desc) else (False,)
    return [(iv, opt) for opt in opts for iv in ivs]


def plan(desc: FunctionDescriptor, fixed_variant: int = 0,
         opt_some: bool = False) -> tuple[DrivePlan | None, str | None]:
    """Returns (plan, None) or (None, undrivable_reason). fixed_variant
    selects the fallback strategy for held-fixed int params; opt_some
    flips synthesizable Option params from None to Some(...) — the
    orchestrator walks variants() when the first ladder call is rejected."""
    if not desc.drivable:
        return None, desc.skip_reason or "not drivable"

    active = [p for p in desc.params if not p.omitted]
    undrivable = [p for p in active if p.spec_type is None]
    if undrivable:
        p = undrivable[0]
        return None, f"unsupported_type: param '{p.name}'" + (
            f" ({p.detail})" if p.detail else "")

    scalable = [p for p in active if p.spec_type in SCALABLE_TAGS]
    collections = [p for p in scalable if p.spec_type not in MAGNITUDE_TAGS]
    ints = [p for p in scalable if p.spec_type in MAGNITUDE_TAGS]
    # non-scalable-but-drivable tags: held at a fixed value, never scaled
    # (a Duration is a timeout — scaling it would measure the sleep)
    fixed_other = [p for p in active if p.spec_type in FIXED_TAGS]

    # a fillable receiver is a collection-class driver: it scales with the
    # joint n, and ints alongside it are held fixed like any collection
    recv_scaled = bool(desc.receiver and desc.receiver_fill)
    if collections:
        drivers, fixed_ints = collections, ints
    elif recv_scaled:
        drivers, fixed_ints = [], ints
    elif ints:
        drivers, fixed_ints = ints, []
    else:
        return None, "no_scalable_params"

    shapes: list[str] = []
    for p in drivers:
        for s in TAG_SHAPES[p.spec_type]:
            if s not in shapes:
                shapes.append(s)
    if recv_scaled and not shapes:
        shapes = ["random"]        # receiver fill is random-content only

    driver_names = ([f"self({desc.receiver_fill})"] if recv_scaled else []) \
        + [p.name for p in drivers]
    first_driver_idx = next((i for i, p in enumerate(active) if p in drivers),
                            None)
    if fixed_variant == 0:
        fixed_desc = 1
    elif fixed_variant == 1:
        fixed_desc = 0
    else:
        fixed_desc = f"half_of:{driver_names[0]}"
    fixed_params = {p.name: fixed_desc for p in fixed_ints}
    has_fixed_ints = bool(fixed_ints)

    def _fixed_display(p):
        if p.spec_type == "instance_":
            return p.type_ref
        if p.spec_type == "opt_none" and opt_some and p.type_ref:
            return p.type_ref                  # e.g. "Some(1usize)"
        return FIXED_TAG_DISPLAY[p.spec_type]

    fixed_params.update({p.name: _fixed_display(p) for p in fixed_other})

    def specs(shape: str, size: int) -> list[GenSpec]:
        out = []
        for p in active:
            if p in drivers:
                s = shape if shape in TAG_SHAPES[p.spec_type] else "random"
                # the param name is part of the seed: co-scaled same-typed
                # args must be DIFFERENT values, or equality-sensitive and
                # early-exit code measures a fiction
                out.append(GenSpec(p.spec_type, s, size,
                                   seed_for(f"{desc.fid}#{p.name}", s, size)))
            elif p.spec_type in FIXED_TAGS:
                tag = p.spec_type
                if tag == "opt_none" and opt_some and p.type_ref:
                    tag = "opt_some"     # harness arm branches on this
                out.append(GenSpec(tag, "fixed",
                                   FIXED_TAGS[p.spec_type],
                                   seed_for(desc.fid, "fixed", 0),
                                   type_ref=p.type_ref))
            elif fixed_variant == 2:
                spec = GenSpec("int_half_of", "magnitude", 0,
                               seed_for(desc.fid, "fixed", 0))
                spec.of_index = first_driver_idx
                out.append(spec)
            else:
                # held-fixed scalar keeps its own tag: a float param must
                # receive a float, not an int that happens to coerce
                out.append(GenSpec(p.spec_type or "int_mag", "magnitude",
                                   fixed_desc,
                                   seed_for(desc.fid, "fixed", 0)))
        if recv_scaled:
            # trailing, so positional arg indices stay stable; sized and
            # seeded like any generated input
            out.append(GenSpec("recv_fill", "random", size,
                               seed_for(f"{desc.fid}#self", "fill", size)))
        return out

    return DrivePlan(driver_params=driver_names, fixed_params=fixed_params,
                     shapes=shapes, specs=specs,
                     has_fixed_ints=has_fixed_ints,
                     has_variants=len(variants(desc)) > 1,
                     receiver_scaled=recv_scaled), None
