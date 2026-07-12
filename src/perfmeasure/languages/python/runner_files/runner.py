"""perfmeasure Python runner.

Executed as `<target_python> -I runner.py --target-root <root>` — under the
TARGET project's interpreter, so imports resolve against the target's own
environment. This file must stay stdlib-only: it cannot import perfmeasure.
The wire format mirrors perfmeasure/protocol.py (kept in sync by the
conformance tests).

Protocol I/O uses a private dup of the original stdout; fd 1 is rebound to
stderr at boot so target code that print()s cannot corrupt the protocol.
"""
from __future__ import annotations

import argparse
import gc
import importlib
import importlib.util
import inspect
import json
import os
import platform
import random
import string
import sys
import time
import traceback
import tracemalloc
import typing

try:
    from types import UnionType          # int | None (PEP 604)
except ImportError:                      # pragma: no cover
    UnionType = None

PROTOCOL_VERSION = 1
SPEC_TYPES = ["list_int", "list_float", "list_str", "list_list_int", "str_",
              "bytes_", "dict_si", "set_int", "int_mag", "bool_"]
SHAPES = ["random", "sorted", "reversed", "dup_heavy", "all_equal", "magnitude"]
BATCH_THRESHOLD_S = 10e-6   # calls faster than this are timed in batches
BATCH_TARGET_S = 200e-6
INNER_LIST_LEN = 16


# --- protocol plumbing --------------------------------------------------------

def _bind_streams():
    proto = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)                 # target prints land on stderr
    sys.stdout = sys.stderr
    return proto


def send(proto, msg):
    proto.write(json.dumps(msg, separators=(",", ":")) + "\n")
    proto.flush()


def error(req_id, fid, kind, message, detail=None):
    return {"op": "error", "id": req_id, "fid": fid, "kind": kind,
            "message": message, "detail": detail or {}, "retryable": False}


# --- discovery ----------------------------------------------------------------

_module_cache: dict[str, object] = {}
_fn_cache: dict[str, object] = {}


def _module_name_for(path: str) -> tuple[str, str]:
    """(sys.path root, dotted module name) — walk up while __init__.py."""
    path = os.path.abspath(path)
    d, base = os.path.split(path)
    parts = [os.path.splitext(base)[0]]
    while os.path.isfile(os.path.join(d, "__init__.py")):
        d, pkg = os.path.split(d)
        parts.insert(0, pkg)
    return d, ".".join(parts)


def _import_file(path: str):
    path = os.path.abspath(path)
    if path in _module_cache:
        return _module_cache[path]
    root, modname = _module_name_for(path)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        mod = importlib.import_module(modname)
    except BaseException:
        # standalone script fallback
        spec = importlib.util.spec_from_file_location(
            "_perfmeasure_target_" + str(len(_module_cache)), path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    _module_cache[path] = mod
    return mod


def _map_hint(hint) -> tuple[str | None, str]:
    """type hint -> (spec_type, detail-if-none)."""
    if hint is inspect.Parameter.empty:
        return None, "missing annotation"
    if hint is bool:                      # before int: bool subclasses int
        return "bool_", ""                # drivable, held fixed — never scaled
    if hint is int:
        return "int_mag", ""
    if hint is str:
        return "str_", ""
    if hint is bytes:
        return "bytes_", ""
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union or (UnionType is not None and origin is UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:            # Optional[T] -> T
            return _map_hint(non_none[0])
        return None, f"union {hint}"
    import collections.abc as abc
    if hint in (list, abc.Sequence, abc.Iterable):
        return "list_int", ""
    if hint is dict or hint is abc.Mapping:
        return "dict_si", ""
    if hint in (set, frozenset):
        return "set_int", ""
    if origin in (list, abc.Sequence, abc.Iterable, abc.Collection):
        if not args or args[0] is int:
            return "list_int", ""
        if args[0] is str:
            return "list_str", ""
        if args[0] is float:
            return "list_float", ""
        if typing.get_origin(args[0]) is list and \
                typing.get_args(args[0])[:1] in ((int,), ()):
            return "list_list_int", ""
        return None, f"element type {args[0]!r}"
    if origin in (dict, abc.Mapping):
        if not args or (args[0] in (str, int) and args[1] is int):
            return "dict_si", ""
        return None, f"dict types {args!r}"
    if origin in (set, frozenset):
        if not args or args[0] is int:
            return "set_int", ""
        return None, f"element type {args[0]!r}"
    return None, f"unsupported type {hint!r}"


def _describe_function(fid, fn):
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError) as e:
        return {"fid": fid, "file": "", "line": 0, "params": [],
                "drivable": False, "skip_reason": f"no signature: {e}"}
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = getattr(fn, "__annotations__", {}) or {}
    params, drivable, reason = [], True, None
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            drivable, reason = False, f"*{p.name}"
            params.append({"name": p.name, "spec_type": None,
                           "omitted": False, "detail": "varargs"})
            continue
        if p.default is not p.empty:
            params.append({"name": p.name, "spec_type": None,
                           "omitted": True, "detail": "has default"})
            continue
        tag, detail = _map_hint(hints.get(p.name, p.annotation))
        params.append({"name": p.name, "spec_type": tag,
                       "omitted": False, "detail": detail})
        if tag is None:
            drivable = False
            reason = reason or f"param '{p.name}': {detail}"
    try:
        line = fn.__code__.co_firstlineno
        file = fn.__code__.co_filename
    except AttributeError:
        line, file = 0, ""
    return {"fid": fid, "file": file, "line": line, "params": params,
            "drivable": drivable, "skip_reason": reason}


def do_discover(req):
    functions, only = [], req.get("only")
    for path in req["files"]:
        try:
            mod = _import_file(path)
        except BaseException:
            functions.append({
                "fid": f"{os.path.abspath(path)}::<module>", "file": path,
                "line": 0, "params": [], "drivable": False,
                "skip_reason": "import_failed: "
                               + traceback.format_exc(limit=3).strip()[-500:]})
            continue
        def _measurable(obj):
            return inspect.isfunction(obj) or (
                callable(obj)                       # e.g. lru_cache wrappers
                and inspect.isfunction(getattr(obj, "__wrapped__", None)))

        for name, fn in inspect.getmembers(mod, _measurable):
            if getattr(fn, "__module__", None) != mod.__name__ \
                    or name.startswith("_"):
                continue
            fid = f"{os.path.abspath(path)}::{fn.__qualname__}"
            if only and fid != only:
                continue
            _fn_cache[fid] = fn
            functions.append(_describe_function(fid, fn))
    return {"op": "result", "id": req["id"], "functions": functions}


def _resolve(fid):
    if fid not in _fn_cache:
        path, _, qualname = fid.partition("::")
        mod = _import_file(path)
        obj = mod
        for part in qualname.split("."):
            obj = getattr(obj, part)
        _fn_cache[fid] = obj
    return _fn_cache[fid]


# --- input materialization ------------------------------------------------------

def _shape_list(base, shape, rng, pool_ratio=16):
    if shape == "sorted":
        return sorted(base)
    if shape == "reversed":
        return sorted(base, reverse=True)
    return base


def materialize(spec):
    tag, shape = spec["spec_type"], spec["shape"]
    size, seed = spec["size"], spec["seed"]
    rng = random.Random(seed)
    if tag == "int_mag":
        return size
    if tag == "bool_":
        return bool(size)
    if tag == "list_int":
        if shape == "all_equal":
            return [7] * size
        if shape == "dup_heavy":
            pool = [rng.randrange(-2**31, 2**31) for _ in range(max(1, size // 16))]
            return [rng.choice(pool) for _ in range(size)]
        return _shape_list([rng.randrange(-2**31, 2**31) for _ in range(size)],
                           shape, rng)
    if tag == "list_float":
        if shape == "all_equal":
            return [0.5] * size
        if shape == "dup_heavy":
            pool = [rng.random() for _ in range(max(1, size // 16))]
            return [rng.choice(pool) for _ in range(size)]
        return _shape_list([rng.random() for _ in range(size)], shape, rng)
    if tag == "list_str":
        if shape == "all_equal":
            return ["xxxxxxxx"] * size
        if shape == "dup_heavy":
            pool = ["".join(rng.choices(string.ascii_letters, k=8))
                    for _ in range(max(1, size // 16))]
            return [rng.choice(pool) for _ in range(size)]
        return _shape_list(["".join(rng.choices(string.ascii_letters, k=8))
                            for _ in range(size)], shape, rng)
    if tag == "list_list_int":
        if shape == "all_equal":
            return [[7] * INNER_LIST_LEN for _ in range(size)]
        return [[rng.randrange(-2**31, 2**31) for _ in range(INNER_LIST_LEN)]
                for _ in range(size)]
    if tag == "str_":
        if shape == "all_equal":
            return "a" * size
        if shape == "dup_heavy":
            return "".join(rng.choices("abcd", k=size))
        s = rng.choices(string.ascii_letters, k=size)
        if shape == "sorted":
            s = sorted(s)
        elif shape == "reversed":
            s = sorted(s, reverse=True)
        return "".join(s)
    if tag == "bytes_":
        if shape == "all_equal":
            return b"a" * size
        b = [rng.randrange(256) for _ in range(size)]
        if shape == "sorted":
            b = sorted(b)
        elif shape == "reversed":
            b = sorted(b, reverse=True)
        return bytes(b)
    if tag == "dict_si":
        if shape == "sorted":
            return {f"k{i:012d}": rng.randrange(2**31) for i in range(size)}
        if shape == "dup_heavy":
            pool = [rng.randrange(64) for _ in range(max(1, size // 16))]
            return {f"k{rng.randrange(2**60):015x}{i}": rng.choice(pool)
                    for i in range(size)}
        return {f"k{rng.randrange(2**60):015x}{i}": rng.randrange(2**31)
                for i in range(size)}
    if tag == "set_int":
        return set(rng.sample(range(max(size * 4, 4)), size))
    raise ValueError(f"unknown spec_type {tag!r}")


# --- measurement ------------------------------------------------------------------

def _materialize_all(specs):
    """Two passes: symbolic int_half_of specs need the other args first."""
    args = [None] * len(specs)
    for i, s in enumerate(specs):
        if s["spec_type"] != "int_half_of":
            args[i] = materialize(s)
    for i, s in enumerate(specs):
        if s["spec_type"] == "int_half_of":
            ref = args[s["of_index"]]
            args[i] = (len(ref) if hasattr(ref, "__len__") else int(ref)) // 2
    return args


def _fingerprint(obj):
    """Cheap structural fingerprint to detect in-place mutation. Sampled,
    not exhaustive — reorderings and growth are what we care about."""
    if isinstance(obj, (str, bytes, int, float, bool, type(None))):
        return None                       # immutable
    if isinstance(obj, list):
        k = len(obj)
        idx = range(0, k, max(1, k // 16))
        return ("list", k, tuple(repr(obj[i])[:24] for i in idx))
    if isinstance(obj, dict):
        items = list(obj.items())[:8]
        return ("dict", len(obj), tuple(repr(i)[:32] for i in items))
    if isinstance(obj, (set, frozenset)):
        return ("set", len(obj))
    return ("opaque", id(obj))


def _deepsize(obj, depth=0):
    """Sampled recursive getsizeof of a return value (blind-spot check)."""
    size = sys.getsizeof(obj, 0)
    if depth >= 3:
        return size
    if isinstance(obj, (list, tuple)):
        sample = obj[:64]
        if sample:
            size += len(obj) * sum(_deepsize(x, depth + 1)
                                   for x in sample) // len(sample)
    elif isinstance(obj, dict):
        sample = list(obj.items())[:64]
        if sample:
            per = sum(_deepsize(k, depth + 1) + _deepsize(v, depth + 1)
                      for k, v in sample) // len(sample)
            size += len(obj) * per
    elif isinstance(obj, (set, frozenset)):
        sample = list(obj)[:64]
        if sample:
            size += len(obj) * sum(_deepsize(x, depth + 1)
                                   for x in sample) // len(sample)
    return size


def do_call(req):
    started = time.perf_counter()
    fid = req["fid"]
    try:
        fn = _resolve(fid)
    except BaseException:
        return error(req["id"], fid, "not_found",
                     traceback.format_exc(limit=2).strip()[-300:])
    specs = req["inputs"]
    try:
        args = _materialize_all(specs)
    except Exception:
        return error(req["id"], fid, "unsupported_input",
                     traceback.format_exc(limit=2).strip()[-300:])

    measure = req.get("measure", ["time"])
    budget_s = req.get("budget_ms", 10_000) / 1000.0
    notes = []
    mutates = False
    try:
        before = [_fingerprint(a) for a in args]
        warmup_seconds = None
        for i in range(req.get("warmup", 1)):
            w0 = time.perf_counter_ns()
            fn(*args)
            w1 = time.perf_counter_ns()
            if i == 0:
                # the first-ever call is the only honest measurement of a
                # memoizing function; the core compares it to later reps
                warmup_seconds = (w1 - w0) / 1e9
        mutates = any(b is not None and _fingerprint(a) != b
                      for a, b in zip(args, before))
        if mutates:
            notes.append("mutates_input")
            args = _materialize_all(specs)   # warmup dirtied them

        timings, batched = [], False
        if "time" in measure:
            gc.collect()
            gc.disable()
            try:
                t0 = time.perf_counter_ns()
                r = fn(*args)
                t1 = time.perf_counter_ns()
                del r
                first = (t1 - t0) / 1e9
                batch = 1
                if first < BATCH_THRESHOLD_S and not mutates:
                    batch = min(10_000, max(1, int(BATCH_TARGET_S / max(first, 1e-9))))
                    batched = True
                timings.append(first if batch == 1 else _timed_batch(fn, args, batch))
                total = timings[-1] * batch
                min_total = req.get("min_total_ms", 30) / 1000.0
                while (len(timings) < req.get("max_repeats", 15)
                       and total < min_total):
                    if time.perf_counter() - started > budget_s:
                        notes.append("budget")
                        break
                    if mutates:
                        args = _materialize_all(specs)  # untimed
                    t = _timed_batch(fn, args, batch)
                    timings.append(t)
                    total += t * batch
            finally:
                gc.enable()

        peak = ret_deepsize = None
        if "memory" in measure:
            if mutates:
                args = _materialize_all(specs)
            gc.collect()
            tracemalloc.start()
            try:
                base = tracemalloc.get_traced_memory()[0]
                tracemalloc.reset_peak()
                r = fn(*args)
                peak = max(0, tracemalloc.get_traced_memory()[1] - base)
            finally:
                tracemalloc.stop()
            try:
                ret_deepsize = _deepsize(r)
            except Exception:
                ret_deepsize = None
            del r
    except BaseException:
        gc.enable()
        return error(req["id"], fid, "exception",
                     traceback.format_exc(limit=5).strip()[-800:])
    return {"op": "result", "id": req["id"], "fid": fid,
            "wall_seconds": timings, "batched": batched,
            "warmup_seconds": warmup_seconds,
            "peak_alloc_bytes": peak, "ret_deepsize": ret_deepsize,
            "mutates": mutates, "repeats_done": len(timings),
            "notes": notes}


def _timed_batch(fn, args, batch):
    t0 = time.perf_counter_ns()
    for _ in range(batch):
        r = fn(*args)
    t1 = time.perf_counter_ns()
    del r
    return (t1 - t0) / 1e9 / batch


# --- main loop ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", default=".")
    opts = parser.parse_args()
    os.chdir(opts.target_root)
    sys.setrecursionlimit(20_000)

    proto = _bind_streams()
    send(proto, {
        "op": "hello", "protocol": PROTOCOL_VERSION, "language": "python",
        "runtime": f"{platform.python_implementation()} "
                   f"{platform.python_version()} ({sys.executable})",
        "capabilities": {"spec_types": SPEC_TYPES, "shapes": SHAPES,
                         "memory": "tracemalloc", "discover": True},
    })
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        op = req.get("op")
        if op == "shutdown":
            break
        elif op == "ping":
            send(proto, {"op": "pong", "id": req.get("id")})
        elif op == "discover":
            send(proto, do_discover(req))
        elif op == "call":
            send(proto, do_call(req))
        else:
            send(proto, error(req.get("id", "?"), None, "internal",
                              f"unknown op {op!r}"))


if __name__ == "__main__":
    main()
