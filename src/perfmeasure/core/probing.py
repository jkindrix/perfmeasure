"""Type probing for unhinted parameters.

Name heuristics order the candidate tags; probing decides: a candidate is
accepted iff the function returns without exception at sizes 4 and 16.
Only parameters with NO annotation are probed — an explicit unsupported
hint (Callable, custom class) is authoritative and stays undrivable.

Probed drives are honest but weaker: every result records
type_source="probed" and confidence is capped at medium — the function
tolerating a list[int] does not prove the measurement is semantically
meaningful, so the generator spec is published for audit.
"""
from __future__ import annotations

import re
import time

from perfmeasure import protocol
from perfmeasure.core.model import FunctionDescriptor, GenSpec
from perfmeasure.session import RunnerSession

PROBE_SIZES = (4, 16)
PROBE_TIMEOUT_S = 5.0

_NAME_HINTS = [
    (re.compile(r"^(n|size|count|num\w*|depth|k|limit|index|idx|i|j|r"
                r"|start|stop|step|offset)$"), "int_mag"),
    (re.compile(r"^(s|text|word|name|pattern|line)$"), "str_"),
    (re.compile(r"^(items|arr|xs|ys|data|nums|values|lst|seq|iterable"
                r"|a|b)$"), "list_int"),
    (re.compile(r"^(d|mapping|table|counts)$"), "dict_si"),
]
_DEFAULT_ORDER = ["list_int", "str_", "int_mag", "dict_si", "list_str", "set_int"]


def candidates_for(param_name: str) -> list[str]:
    order = []
    for pat, tag in _NAME_HINTS:
        if pat.match(param_name):
            order.append(tag)
            break
    order += [t for t in _DEFAULT_ORDER if t not in order]
    return order


def probe(session: RunnerSession, desc: FunctionDescriptor,
          deadline: float | None = None) -> tuple[bool, str | None]:
    """Resolve unhinted params in place. Returns (any_probed, fail_reason).
    `deadline` is an absolute perf_counter timestamp shared with the rest
    of the measurement: each probe's timeout is recomputed from it, so
    probing can never blow the function deadline on its own."""
    unhinted = [p for p in desc.params
                if not p.omitted and p.spec_type is None
                and p.detail == "missing annotation"]
    if not unhinted:
        return False, None
    # explicit unsupported hints elsewhere: probing can't rescue those
    if any(p.spec_type is None and not p.omitted
           and p.detail != "missing annotation" for p in desc.params):
        return False, None

    resolved: dict[str, str] = {p.name: p.spec_type for p in desc.params
                                if p.spec_type}
    for target in unhinted:
        last_error = "no candidates"
        for tag in candidates_for(target.name):
            if _accepts(session, desc, resolved, target.name, tag, deadline):
                resolved[target.name] = tag
                target.spec_type = tag
                target.detail = "probed"
                break
            last_error = f"candidate {tag} rejected"
        else:
            others = [p.name for p in unhinted
                      if p.name != target.name and p.name not in resolved]
            blame = (f"; co-probed unhinted params {others} may be the "
                     "real blockers" if others else "")
            return False, (f"rejected: param '{target.name}' accepted no "
                           f"generated input ({last_error}){blame}")
    desc.drivable = True
    desc.skip_reason = None
    return True, None


def _accepts(session, desc, resolved, target_name, target_tag,
             deadline=None) -> bool:
    for size in PROBE_SIZES:
        timeout = PROBE_TIMEOUT_S
        if deadline is not None:
            timeout = max(0.25, min(PROBE_TIMEOUT_S,
                                    deadline - time.perf_counter()))
        specs = []
        for p in desc.params:
            if p.omitted:
                continue
            tag = (target_tag if p.name == target_name
                   else resolved.get(p.name) or candidates_for(p.name)[0])
            shape = "magnitude" if tag == "int_mag" else "random"
            specs.append(GenSpec(
                tag, shape, size,
                protocol.seed_for(f"{desc.fid}#{p.name}", "probe", size)
            ).wire())
        resp = session.request(
            protocol.call_msg(session.next_id(), desc.fid, specs, warmup=0,
                              max_repeats=1, min_total_ms=0,
                              budget_ms=int(timeout * 400)),
            timeout=timeout)
        if resp["op"] != "result":
            return False
    return True
