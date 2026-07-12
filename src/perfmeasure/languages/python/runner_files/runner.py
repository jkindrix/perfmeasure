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
import pathlib
import platform
import random
import string
import struct
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
              "bytes_", "dict_si", "dict_ii", "set_int", "int_mag", "bool_"]
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
    mod = None
    try:
        candidate = importlib.import_module(modname)
        # a target file named like a stdlib/installed module (random.py,
        # pathlib.py) silently wins the sys.modules race — accept the
        # import only if it actually loaded THIS file
        loaded = getattr(candidate, "__file__", None)
        if loaded and os.path.realpath(loaded) == os.path.realpath(path):
            mod = candidate
    except BaseException:
        pass
    if mod is None:
        # load under a unique private name, registered so classes defined
        # here stay importable by module name (instance_ construction)
        spec = importlib.util.spec_from_file_location(
            "_perfmeasure_target_" + str(len(_module_cache)), path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    _module_cache[path] = mod
    return mod


def _map_hint(hint) -> tuple[str | None, str]:
    """type hint -> (spec_type, detail-if-none)."""
    if hint is inspect.Parameter.empty:
        return None, "missing annotation"
    if hint is bool:                      # before int: bool subclasses int
        return "bool_", ""                # drivable, held fixed — never scaled
    if isinstance(hint, type) and issubclass(hint, (pathlib.PurePath, os.PathLike)):
        # the biggest cross-project bucket: honest label, not "unsupported"
        return None, "filesystem path (I/O domain, not generated)"
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
        if not args or args == (str, int):
            return "dict_si", ""
        if args == (int, int):
            return "dict_ii", ""
        return None, f"dict types {args!r}"
    if origin in (set, frozenset):
        if not args or args[0] is int:
            return "set_int", ""
        return None, f"element type {args[0]!r}"
    if inspect.isclass(hint) and hint.__module__ not in ("builtins", "typing"):
        # user-defined class: constructible => a fixed instance
        if _constructible(hint):
            return "instance_", f"{hint.__module__}:{hint.__qualname__}"
        return None, (f"no synthesizable constructor for {hint.__qualname__}")
    return None, f"unsupported type {hint!r}"


_ctor_cache: dict[type, bool] = {}


def _constructible(cls) -> bool:
    if cls not in _ctor_cache:
        try:
            _synth_instance(cls)
            _ctor_cache[cls] = True
        except Exception:
            _ctor_cache[cls] = False
    return _ctor_cache[cls]


def _synth_instance(cls, depth=0):
    """Zero-arg construction first; else synthesize the required __init__
    args from type hints (small scalars, empty containers, None for
    Optionals, recursion for class-typed args). Deterministic, so the
    same instance state is rebuilt on every call op. Raises on failure."""
    try:
        return cls()
    except Exception:
        if depth >= 2:
            raise
    sig = inspect.signature(cls.__init__)
    try:
        hints = typing.get_type_hints(cls.__init__)
    except Exception:
        hints = getattr(cls.__init__, "__annotations__", {}) or {}
    kwargs = {}
    for p in list(sig.parameters.values())[1:]:          # skip self
        if p.default is not p.empty or \
                p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        kwargs[p.name] = _synth_value(hints.get(p.name, p.annotation), depth)
    return cls(**kwargs)


def _synth_value(hint, depth):
    if hint is inspect.Parameter.empty:
        raise ValueError("cannot synthesize an unhinted required argument")
    if hint is bool:
        return False
    if hint is int:
        return 1
    if hint is float:
        return 1.0
    if hint is str:
        return "x"
    if hint is bytes:
        return b"x"
    origin = typing.get_origin(hint)
    if origin is typing.Union or (UnionType is not None
                                  and origin is UnionType):
        if type(None) in typing.get_args(hint):
            return None
        return _synth_value(typing.get_args(hint)[0], depth)
    import collections.abc as abc
    if hint is list or origin in (list, abc.Sequence, abc.Iterable):
        return []
    if hint is dict or origin in (dict, abc.Mapping):
        return {}
    if hint is set or origin in (set, frozenset):
        return set()
    if hint is tuple or origin is tuple:
        return ()
    if inspect.isclass(hint) and hint.__module__ not in ("builtins", "typing"):
        return _synth_instance(hint, depth + 1)
    raise ValueError(f"cannot synthesize a value for {hint!r}")


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
    plist = list(sig.parameters.values())
    if plist and plist[0].name in ("self", "cls") \
            and plist[0].annotation is plist[0].empty:
        # an unbound method pasted as a free function: probing it with
        # generated 'self' values would only measure garbage
        return {"fid": fid, "file": base_file(fn), "line": base_line(fn),
                "params": [], "drivable": False,
                "skip_reason": "unbound method (self/cls parameter)"}
    for p in plist:
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
        entry = {"name": p.name, "spec_type": tag,
                 "omitted": False, "detail": detail}
        if tag == "instance_":       # detail slot carries the constructor ref
            entry["type_ref"], entry["detail"] = detail, ""
        params.append(entry)
        if tag is None:
            drivable = False
            reason = reason or f"param '{p.name}': {detail}"
    return {"fid": fid, "file": base_file(fn), "line": base_line(fn),
            "params": params, "drivable": drivable, "skip_reason": reason}


def base_file(fn):
    try:
        return fn.__code__.co_filename
    except AttributeError:
        return ""


def base_line(fn):
    try:
        return fn.__code__.co_firstlineno
    except AttributeError:
        return 0


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
        for cname, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ != mod.__name__ or cname.startswith("_"):
                continue
            cfid = f"{os.path.abspath(path)}::{cls.__qualname__}"
            if not _constructible(cls):
                if not only:
                    functions.append({
                        "fid": cfid, "file": path, "line": 0, "params": [],
                        "drivable": False,
                        "skip_reason": "methods unreachable: no "
                                       "synthesizable constructor"})
                continue
            inst = _synth_instance(cls)
            for mname, m in inspect.getmembers(inst, callable):
                if mname.startswith("_") or not (
                        inspect.ismethod(m) or inspect.isfunction(m)):
                    continue
                if getattr(m, "__module__", None) != mod.__name__:
                    continue        # inherited from elsewhere
                fid = f"{cfid}.{mname}"
                if only and fid != only:
                    continue
                desc = _describe_function(fid, m)   # bound: self already gone
                desc["receiver"] = f"{cls.__module__}:{cls.__qualname__}"
                functions.append(desc)
    return {"op": "result", "id": req["id"], "functions": functions}


def _resolve(fid):
    if fid not in _fn_cache:
        path, _, qualname = fid.partition("::")
        mod = _import_file(path)
        obj, prev = mod, None
        for part in qualname.split("."):
            prev, obj = obj, getattr(obj, part)
        if inspect.isclass(prev):
            # method: bind to a FRESH zero-arg instance per call op, so
            # receiver state never leaks across sizes/shapes
            _fn_cache[fid] = ("method", prev, qualname.rsplit(".", 1)[1])
        else:
            _fn_cache[fid] = obj
    return _fn_cache[fid]


def _callable_for(fid):
    fn = _resolve(fid)
    if isinstance(fn, tuple):
        _, cls, name = fn
        return getattr(_synth_instance(cls), name)
    return fn


def _receiver_fingerprint(fn):
    inst = getattr(fn, "__self__", None)
    if inst is None or not hasattr(inst, "__dict__"):
        return None
    try:
        return repr(sorted(inst.__dict__.items(), key=lambda kv: kv[0]))[:4096]
    except Exception:
        return None


# --- input materialization ------------------------------------------------------

def _shape_list(base, shape, rng, pool_ratio=16):
    if shape == "sorted":
        return sorted(base)
    if shape == "reversed":
        return sorted(base, reverse=True)
    return base


# fast bulk generators: randbytes + struct beats per-element randrange by
# ~20x, and materialization (not measurement) dominated wall time at the
# ladder's top sizes
def _rand_i64s(rng, n):
    return list(struct.unpack(f"<{n}q", rng.randbytes(8 * n)))


def _rand_floats(rng, n):
    return [u / 2**64 for u in struct.unpack(f"<{n}Q", rng.randbytes(8 * n))]


_ASCII_TABLE = bytes(97 + (b % 26) for b in range(256))
_ABCD_TABLE = bytes(97 + (b % 4) for b in range(256))


def materialize(spec):
    tag, shape = spec["spec_type"], spec["shape"]
    size, seed = spec["size"], spec["seed"]
    rng = random.Random(seed)
    if tag == "int_mag":
        return size
    if tag == "bool_":
        return bool(size)
    if tag == "instance_":
        modname, _, qual = spec["type_ref"].partition(":")
        obj = importlib.import_module(modname)
        for part in qual.split("."):
            obj = getattr(obj, part)
        return _synth_instance(obj)
    if tag == "list_int":
        if shape == "all_equal":
            return [7] * size
        if shape == "dup_heavy":
            pool = _rand_i64s(rng, max(1, size // 16))
            idx = rng.randbytes(size)
            return [pool[b % len(pool)] for b in idx]
        return _shape_list(_rand_i64s(rng, size), shape, rng)
    if tag == "list_float":
        if shape == "all_equal":
            return [0.5] * size
        if shape == "dup_heavy":
            pool = _rand_floats(rng, max(1, size // 16))
            idx = rng.randbytes(size)
            return [pool[b % len(pool)] for b in idx]
        return _shape_list(_rand_floats(rng, size), shape, rng)
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
        if shape == "dup_heavy":
            pool = [[rng.randrange(-2**31, 2**31)
                     for _ in range(INNER_LIST_LEN)]
                    for _ in range(max(1, size // 16))]
            return [list(rng.choice(pool)) for _ in range(size)]
        return [[rng.randrange(-2**31, 2**31) for _ in range(INNER_LIST_LEN)]
                for _ in range(size)]
    if tag == "str_":
        if shape == "all_equal":
            return "a" * size
        if shape == "dup_heavy":
            return rng.randbytes(size).translate(_ABCD_TABLE).decode("ascii")
        raw = rng.randbytes(size).translate(_ASCII_TABLE)
        if shape == "sorted":
            raw = bytes(sorted(raw))
        elif shape == "reversed":
            raw = bytes(sorted(raw, reverse=True))
        return raw.decode("ascii")
    if tag == "bytes_":
        if shape == "all_equal":
            return b"a" * size
        if shape == "dup_heavy":
            return rng.randbytes(size).translate(_ABCD_TABLE)
        b = rng.randbytes(size)
        if shape == "sorted":
            b = bytes(sorted(b))
        elif shape == "reversed":
            b = bytes(sorted(b, reverse=True))
        return b
    if tag == "dict_si":
        if shape == "sorted":
            return {f"k{i:012d}": rng.randrange(2**31) for i in range(size)}
        if shape == "dup_heavy":
            pool = [rng.randrange(64) for _ in range(max(1, size // 16))]
            return {f"k{rng.randrange(2**60):015x}{i}": rng.choice(pool)
                    for i in range(size)}
        return {f"k{rng.randrange(2**60):015x}{i}": rng.randrange(2**31)
                for i in range(size)}
    if tag == "dict_ii":
        if shape == "sorted":
            return {i: rng.randrange(2**31) for i in range(size)}
        if shape == "dup_heavy":
            pool = [rng.randrange(64) for _ in range(max(1, size // 16))]
            keys = rng.sample(range(max(size * 4, 4)), size)
            return {k: rng.choice(pool) for k in keys}
        return {k: rng.randrange(2**31)
                for k in rng.sample(range(max(size * 4, 4)), size)}
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
        fn = _callable_for(fid)
    except AttributeError:
        return error(req["id"], fid, "not_found",
                     traceback.format_exc(limit=2).strip()[-300:])
    except BaseException:
        return error(req["id"], fid, "exception",
                     traceback.format_exc(limit=3).strip()[-500:])
    specs = req["inputs"]
    try:
        args = _materialize_all(specs)
    except Exception:
        return error(req["id"], fid, "unsupported_input",
                     traceback.format_exc(limit=2).strip()[-300:])

    measure = req.get("measure", ["time"])
    budget_s = req.get("budget_ms", 10_000) / 1000.0
    notes = []
    mutates = recv_mutates = False
    try:
        before = [_fingerprint(a) for a in args]
        recv_before = _receiver_fingerprint(fn)
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
        recv_mutates = (recv_before is not None
                        and _receiver_fingerprint(fn) != recv_before)
        if mutates:
            notes.append("mutates_input")
            args = _materialize_all(specs)   # warmup dirtied them
        if recv_mutates:
            # the method mutates self: bind a FRESH instance per rep
            # (untimed) so receiver state never accumulates across reps
            notes.append("mutates_receiver")
            fn = _callable_for(fid)

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
                if first < BATCH_THRESHOLD_S and not mutates \
                        and not recv_mutates:
                    batch = min(10_000, max(1, int(BATCH_TARGET_S / max(first, 1e-9))))
                    batched = True
                timings.append(first if batch == 1 else _timed_batch(fn, args, batch))
                total = timings[-1] * batch
                min_total = req.get("min_total_ms", 10) / 1000.0
                while (len(timings) < req.get("max_repeats", 15)
                       and total < min_total):
                    if time.perf_counter() - started > budget_s:
                        notes.append("budget")
                        break
                    if mutates:
                        args = _materialize_all(specs)  # untimed
                    if recv_mutates:
                        fn = _callable_for(fid)         # untimed
                    t = _timed_batch(fn, args, batch)
                    timings.append(t)
                    total += t * batch
            finally:
                gc.enable()

        peak = ret_deepsize = None
        if "memory" in measure:
            if mutates:
                args = _materialize_all(specs)
            if recv_mutates:
                fn = _callable_for(fid)
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
            "mutates": mutates, "mutates_receiver": recv_mutates,
            "repeats_done": len(timings),
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
