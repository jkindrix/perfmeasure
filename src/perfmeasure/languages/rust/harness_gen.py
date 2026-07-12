"""Generate, cache, and build the per-crate measurement harness.

The harness is a standalone crate (own [workspace], path dependency on the
target) whose main.rs = static template + one generated dispatch arm per
drivable function. Compile errors from over-eager textual discovery are
parsed from cargo's JSON messages, the offending arms dropped
(skip_reason: harness_compile_failed), and the build retried once — the
pressure valve that lets discovery be honest instead of perfect.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from importlib import resources
from pathlib import Path

from perfmeasure.languages.rust.discover import DECL_TYPES

CACHE_ROOT = Path.home() / ".cache" / "perfmeasure" / "rust"

CARGO_TOML = """\
[package]
name = "perfmeasure_harness"
version = "0.0.0"
edition = "2021"

[dependencies]
{crate} = {{ path = "{path}"{features} }}
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"
libc = "0.2"

[profile.release]
panic = "unwind"
debug-assertions = false
overflow-checks = false
lto = false
codegen-units = 16

[workspace]
"""

_GEN = {"list_int": "shaped_i64", "list_str": "gen_list_str",
        "str_": "gen_string", "bytes_": "gen_bytes",
        "dict_ii": "gen_map_ii", "dict_si": "gen_map_si",
        "list_float": "gen_list_f64", "list_list_int": "gen_list_list",
        "set_int": "gen_set", "bool_": "gen_bool",
        "duration_ms": "gen_duration"}


def _template() -> str:
    ref = resources.files("perfmeasure.languages.rust") \
        / "harness_template" / "main_template.rs"
    return ref.read_text()


def _arm(fn: dict) -> str:
    fid = fn["fid"]
    lines = [f'        // ARM {fid}', f'        "{fid}" => {{']
    own: list[str] = []                      # prep expressions, tuple-ordered
    exprs: list[str] = []
    receiver = fn.get("receiver")
    if receiver:
        lines.append(f"            let __recv = {receiver};")
    for i, p in enumerate(fn["params"]):
        tag, style, rtype = p["spec_type"], p["style"], p["rust_type"]
        cast = p.get("cast")
        if style == "none":                  # Option<T>: None type-infers
            exprs.append("None")
            continue
        if style == "borrow_ctor":           # fixed default instance
            lines.append(f"            let a{i} = {p['type_ref']};")
            exprs.append(f"&a{i}")
            continue
        if style == "own_ctor":              # consumed: fresh instance per rep
            exprs.append(f"__p.{len(own)}")
            own.append(p["type_ref"])
            continue
        if cast:                             # other-width int/float slices
            base_gen = "shaped_i64" if tag == "list_int" else "gen_list_f64"
            lines.append(
                f"            let a{i}: Vec<{cast}> = "
                f"{base_gen}(&req.inputs[{i}]).into_iter()"
                f".map(|v| v as {cast}).collect();")
            if style == "own":
                exprs.append(f"__p.{len(own)}")
                own.append(f"a{i}.clone()")
            else:
                exprs.append(f"&a{i}[..]")
            continue
        if tag == "int_mag":
            lines.append(
                f'            let a{i}: i64 = '
                f'if req.inputs[{i}].spec_type == "int_half_of" '
                f'{{ resolve_half_of(&req.inputs, &sizes, '
                f'req.inputs[{i}].of_index.unwrap_or(0)) }} '
                f'else {{ gen_int(&req.inputs[{i}]) }};')
            if rtype == "i64":
                exprs.append(f"a{i}")
            else:
                lines.append(
                    f'            let a{i}t: {rtype} = match a{i}.try_into() '
                    f'{{ Ok(v) => v, Err(_) => return error_json(&req.id, '
                    f'&req.fid, "unsupported_input", '
                    f'"int exceeds {rtype}") }};')
                exprs.append(f"a{i}t")
        else:
            lines.append(f"            let a{i}: {DECL_TYPES[tag]} = "
                         f"{_GEN[tag]}(&req.inputs[{i}]);")
            if style == "own":
                exprs.append(f"__p.{len(own)}")
                own.append(f"a{i}.clone()")
            elif style == "borrow_slice":
                exprs.append(f"&a{i}[..]")
            elif style == "borrow_str_slice":   # &[&str] view over Vec<String>
                lines.append(f"            let a{i}r: Vec<&str> = "
                             f"a{i}.iter().map(|s| s.as_str()).collect();")
                exprs.append(f"&a{i}r[..]")
            elif style == "copy":                # bool, Duration
                exprs.append(f"a{i}")
            else:
                exprs.append(f"&a{i}")
    if own:
        prep = "|| (" + ", ".join(own) + ",)"
        head = "|__p|"
    else:
        prep = "|| ()"
        head = "|_|"
    target = (f"__recv.{fid.rsplit('::', 1)[1]}" if receiver else fid)
    call = (f"{head} {{ black_box({target}("
            + ", ".join(f"black_box({e})" for e in exprs) + ")); }")
    lines.append(f"            result_json(&req, run_measured(&req, "
                 f"{str(bool(own)).lower()}, {prep}, {call}))")
    lines.append("        }")
    return "\n".join(lines)


def generate_main(functions: list[dict], crate: str) -> str:
    arms = "\n".join(_arm(f) for f in functions if f["drivable"])
    return _template().replace("// {{DISPATCH_ARMS}}", arms) \
                      .replace("{{TARGET_CRATE}}", crate)


MAX_CACHE_ENTRIES = 8


def cache_key(crate_root: Path, features: list[str]) -> str:
    """One harness dir per (crate, features). Signatures and target source
    are deliberately NOT in the key: cargo's own fingerprinting is the
    staleness oracle — we always run `cargo build`, and an unchanged tree
    is a ~1s no-op. Hash-and-skip-cargo (the old scheme) silently served
    binaries built from OLD target code after signature-preserving edits."""
    h = hashlib.sha256()
    h.update(str(crate_root.resolve()).encode())
    h.update(repr(sorted(features)).encode())
    return h.hexdigest()[:16]


def _prune_cache(keep: Path) -> None:
    """Old-layout dirs and all-but-the-newest v2 entries are deleted.
    Harness target trees run ~0.5 GB each; unbounded growth was measured
    at 8.8 GB on one machine."""
    import shutil
    root = keep.parent
    if not root.exists():
        return
    for old in root.parent.glob("[0-9a-f]" * 16):   # pre-v2 layout
        shutil.rmtree(old, ignore_errors=True)
    entries = sorted((d for d in root.iterdir() if d.is_dir() and d != keep),
                     key=lambda d: d.stat().st_mtime, reverse=True)
    for stale in entries[MAX_CACHE_ENTRIES - 1:]:
        shutil.rmtree(stale, ignore_errors=True)


def build_harness(crate_root: Path, crate: str, functions: list[dict],
                  features: list[str] | None = None, log=print) -> Path:
    """Returns the built binary path. Mutates `functions`: arms the compiler
    rejects get drivable=False + skip_reason=harness_compile_failed.

    cargo build ALWAYS runs — it is the only correct staleness check for
    the path-dependency's source. The compile-retry drop list is keyed to
    the hash of the full generated dispatch, so a discovery change
    invalidates stale drops automatically."""
    features = features or []
    harness = CACHE_ROOT / "v2" / cache_key(crate_root, features)
    binary = harness / "target" / "release" / "perfmeasure_harness"
    dropped_file = harness / "dropped.json"
    harness.mkdir(parents=True, exist_ok=True)
    _prune_cache(harness)
    (harness / "src").mkdir(exist_ok=True)
    feat = ""
    if features:
        feat = ", features = [" + ", ".join(f'"{f}"' for f in features) + "]"
    (harness / "Cargo.toml").write_text(
        CARGO_TOML.format(crate=crate, path=crate_root.resolve(),
                          features=feat))

    full_main = generate_main(functions, crate)
    source_hash = hashlib.sha256(full_main.encode()).hexdigest()
    all_dropped: set[str] = set()
    if dropped_file.exists():
        try:
            record = json.loads(dropped_file.read_text())
            if record.get("source_hash") == source_hash:
                all_dropped = set(record.get("dropped", []))
                _apply_drops(functions, all_dropped)
        except (ValueError, KeyError):
            pass

    first_build = not binary.exists()
    for attempt in range(2):
        main_rs = generate_main(functions, crate)
        main_path = harness / "src" / "main.rs"
        if not main_path.exists() or main_path.read_text() != main_rs:
            main_path.write_text(main_rs)
        if first_build:
            log("# building measurement harness — first build per crate "
                "is slow, incremental after")
        proc = subprocess.run(
            ["cargo", "build", "--release", "--message-format=json"],
            cwd=harness, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0:
            dropped_file.write_text(json.dumps(
                {"source_hash": source_hash, "dropped": sorted(all_dropped)}))
            return binary
        bad_fids = _failing_arms(proc.stdout, main_rs)
        if not bad_fids or attempt == 1:
            raise RuntimeError(
                "harness build failed:\n" + proc.stderr[-2000:])
        all_dropped |= bad_fids
        _apply_drops(functions, bad_fids)
        log(f"# dropped {len(bad_fids)} function(s) the compiler rejected: "
            + ", ".join(sorted(bad_fids)))
    raise RuntimeError("unreachable")


def _apply_drops(functions: list[dict], fids: set[str]) -> None:
    for f in functions:
        if f["fid"] in fids and f["drivable"]:
            f["drivable"] = False
            f["skip_reason"] = "harness_compile_failed"


def _failing_arms(cargo_json: str, main_rs: str) -> set[str]:
    """Map compiler error spans in main.rs back to // ARM markers."""
    arm_at_line: list[tuple[int, str]] = []
    for lineno, line in enumerate(main_rs.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("// ARM "):
            arm_at_line.append((lineno, stripped[len("// ARM "):]))
    bad: set[str] = set()
    for line in cargo_json.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("reason") != "compiler-message":
            continue
        if msg["message"].get("level") != "error":
            continue
        for span in msg["message"].get("spans", []):
            if not span.get("file_name", "").endswith("main.rs"):
                continue
            errline = span.get("line_start", 0)
            owner = None
            for start, fid in arm_at_line:
                if start <= errline:
                    owner = fid
                else:
                    break
            if owner:
                bad.add(owner)
    return bad
