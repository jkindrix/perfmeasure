"""Result model: everything the tool reports is one of these records.

Complexity classes are totally ordered by growth so "worst across shapes"
is a max(). Provenance is a first-class part of every answer: the tool
never emits an unlabeled guess and never silently omits a function.
"""
from __future__ import annotations

import math
import platform
from dataclasses import dataclass, field
from typing import Any, Callable

TOOL_VERSION = "0.2.0"

# --- complexity classes, ordered by growth ---------------------------------

CLASSES: list[tuple[str, Callable[[float], float]]] = [
    ("O(1)", lambda n: 1.0),
    ("O(log n)", lambda n: math.log2(n) if n > 1 else 1.0),
    ("O(n)", lambda n: n),
    ("O(n log n)", lambda n: n * math.log2(n) if n > 1 else n),
    ("O(n^2)", lambda n: n * n),
    ("O(n^3)", lambda n: n ** 3),
    ("O(2^n)", lambda n: 2.0 ** n),
]
CLASS_ORDER = {name: i for i, (name, _) in enumerate(CLASSES)}


def worst_class(names: list[str]) -> str:
    return max(names, key=CLASS_ORDER.__getitem__)


# --- provenance / confidence ------------------------------------------------

MEASURED = "MEASURED"
AMBIGUOUS = "AMBIGUOUS"
UNDRIVABLE = "UNDRIVABLE"
TIMEOUT = "TIMEOUT"
ERROR = "ERROR"

CONFIDENCES = ["low", "med", "high"]


def lower_confidence(conf: str, steps: int = 1) -> str:
    return CONFIDENCES[max(0, CONFIDENCES.index(conf) - steps)]


# --- input specs -------------------------------------------------------------

SHAPES = ["random", "sorted", "reversed", "dup_heavy", "all_equal"]

# which shapes each type tag supports; unsupported → runner uses "random"
TAG_SHAPES: dict[str, list[str]] = {
    "list_int": SHAPES,
    "list_float": SHAPES,
    "list_str": SHAPES,
    "list_list_int": ["random", "dup_heavy", "all_equal"],
    "str_": SHAPES,
    "bytes_": ["random", "sorted", "reversed", "dup_heavy", "all_equal"],
    "dict_si": ["random", "sorted", "dup_heavy"],   # str keys -> int values
    "dict_ii": ["random", "sorted", "dup_heavy"],   # int keys -> int values
    "set_int": ["random"],
    "int_mag": ["magnitude"],
}
SCALABLE_TAGS = set(TAG_SHAPES)


@dataclass
class GenSpec:
    """Abstract input: the only thing that crosses the pipe (never values)."""
    type_tag: str
    shape: str
    size: int
    seed: int
    of_index: int | None = None    # int_half_of: which arg's size to halve
    type_ref: str | None = None    # instance_: language-native constructor ref

    def wire(self) -> dict[str, Any]:
        msg = {"spec_type": self.type_tag, "shape": self.shape,
               "size": self.size, "seed": self.seed}
        if self.of_index is not None:
            msg["of_index"] = self.of_index
        if self.type_ref is not None:
            msg["type_ref"] = self.type_ref
        return msg


# --- function descriptors (from runner discovery) ----------------------------

@dataclass
class ParamInfo:
    name: str
    spec_type: str | None      # None => not drivable via this param
    omitted: bool = False      # has a default; not passed at all
    detail: str = ""           # why not drivable, when spec_type is None
    type_ref: str | None = None  # instance_: how the runner constructs it


@dataclass
class FunctionDescriptor:
    fid: str                   # opaque, language-native; round-tripped verbatim
    file: str
    line: int
    params: list[ParamInfo]
    drivable: bool
    skip_reason: str | None = None
    receiver: str | None = None   # methods: constructor ref for the instance


@dataclass
class DrivePlan:
    driver_params: list[str]           # co-scaled with the joint n
    fixed_params: dict[str, Any]       # name -> fixed spec description
    shapes: list[str]                  # shapes to sweep
    specs: Callable[[str, int], list[GenSpec]] | None = None


# --- measurement points and fits ---------------------------------------------

@dataclass
class Point:
    n: int
    seconds: float             # min of reps
    reps: int
    peak_bytes: int | None = None
    batched: bool = False      # sub-resolution call timed in a batch loop
    first_seconds: float = 0.0  # first rep
    warmup_seconds: float | None = None  # first-ever call — honest for memoizers
    ret_deepsize: int | None = None  # sampled deep-size of the return value


@dataclass
class FitResult:
    cls: str | None                    # winner, None if unfittable
    candidates: list[str]              # ambiguity set (worst first), == [cls] when clean
    margin: float | None               # log10-rmse gap, winner vs runner-up
    reason: str = ""                   # why unfittable / why ambiguous


@dataclass
class ShapeResult:
    shape: str
    points: list[Point]
    time_fit: FitResult | None = None
    space_fit: FitResult | None = None
    stop_reason: str = ""
    failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FunctionReport:
    fid: str
    file: str
    line: int
    provenance: str
    provenance_detail: str | None = None
    time_cls: str | None = None
    time_candidates: list[str] = field(default_factory=list)
    time_worst_shape: str | None = None
    space_cls: str | None = None
    space_candidates: list[str] = field(default_factory=list)
    space_worst_shape: str | None = None
    confidence: str = "high"
    driver_params: list[str] = field(default_factory=list)
    fixed_params: dict[str, Any] = field(default_factory=dict)
    type_source: dict[str, str] = field(default_factory=dict)
    per_shape: list[ShapeResult] = field(default_factory=list)
    wall_used_s: float = 0.0
    max_n_reached: int = 0
    flags: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    allocator: str = "tracemalloc"   # runner's declared memory semantics

    def to_json(self) -> dict[str, Any]:
        return {
            "function": {"fid": self.fid, "file": self.file, "line": self.line},
            "provenance": self.provenance,
            "provenance_detail": self.provenance_detail,
            "time": {
                "cls": self.time_cls,
                "candidates": self.time_candidates,
                "worst_shape": self.time_worst_shape,
            },
            "space": {
                "cls": self.space_cls,
                "candidates": self.space_candidates,
                "worst_shape": self.space_worst_shape,
                "allocator_visibility": self.allocator,
            },
            "confidence": self.confidence,
            "drive": {
                "driver_params": self.driver_params,
                "fixed_params": self.fixed_params,
                "type_source": self.type_source,
                "n_semantics": "joint size of driver params",
                "seed_scheme": "sha256(fid#param,shape,size)",
            },
            "per_shape": [
                {
                    "shape": s.shape,
                    "time": _fit_json(s.time_fit),
                    "space": _fit_json(s.space_fit),
                    "stop_reason": s.stop_reason,
                    "points": [
                        {"n": p.n, "seconds": p.seconds, "reps": p.reps,
                         "first_seconds": p.first_seconds,
                         "warmup_seconds": p.warmup_seconds,
                         "peak_bytes": p.peak_bytes,
                         "ret_deepsize": p.ret_deepsize,
                         "batched": p.batched}
                        for p in s.points
                    ],
                    "failures": s.failures,
                }
                for s in self.per_shape
            ],
            "budget": {"wall_used_s": round(self.wall_used_s, 3),
                       "max_n_reached": self.max_n_reached},
            "flags": self.flags,
            "environment": {**self.environment,
                            "tool_version": TOOL_VERSION,
                            "platform": platform.platform()},
        }


def _fit_json(fit: "FitResult | None") -> dict[str, Any] | None:
    if fit is None:
        return None
    return {"cls": fit.cls, "candidates": fit.candidates,
            "margin": fit.margin, "reason": fit.reason}
