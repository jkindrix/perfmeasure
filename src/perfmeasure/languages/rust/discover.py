"""Rust discovery: tree-sitter scan for crate-root-reachable pub fns whose
parameter types are on the drivable whitelist.

Deliberately textual — types are string-matched after normalization, never
resolved. A type alias makes a function undrivable; that is correct and
honest, because resolving it is the semantic-model trap this architecture
refuses. The compile-retry loop in harness_gen is the pressure valve for
the cases textual matching gets wrong.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import tree_sitter_rust
from tree_sitter import Language, Parser

RUST = Language(tree_sitter_rust.language())

# normalized type string -> (spec tag, passing style)
# style: "borrow_slice" &v[..] | "borrow" &v | "own" clone per call | "copy"
TYPE_WHITELIST: dict[str, tuple[str, str]] = {
    "&[i64]": ("list_int", "borrow_slice"),
    "&Vec<i64>": ("list_int", "borrow"),
    "Vec<i64>": ("list_int", "own"),
    "&[String]": ("list_str", "borrow_slice"),
    "&Vec<String>": ("list_str", "borrow"),
    "Vec<String>": ("list_str", "own"),
    "&str": ("str_", "borrow"),
    "String": ("str_", "own"),
    "&[u8]": ("bytes_", "borrow_slice"),
    "&Vec<u8>": ("bytes_", "borrow"),
    "Vec<u8>": ("bytes_", "own"),
    "&[f64]": ("list_float", "borrow_slice"),
    "&Vec<f64>": ("list_float", "borrow"),
    "Vec<f64>": ("list_float", "own"),
    "&[Vec<i64>]": ("list_list_int", "borrow_slice"),
    "Vec<Vec<i64>>": ("list_list_int", "own"),
    "&HashSet<i64>": ("set_int", "borrow"),
    "HashSet<i64>": ("set_int", "own"),
    "&[&str]": ("list_str", "borrow_str_slice"),
    "bool": ("bool_", "copy"),
    "Duration": ("duration_ms", "copy"),
    "usize": ("int_mag", "copy"),
    "u64": ("int_mag", "copy"),
    "i64": ("int_mag", "copy"),
    "u32": ("int_mag", "copy"),
    "i32": ("int_mag", "copy"),
    "&HashMap<i64,i64>": ("dict_si", "borrow"),
    "HashMap<i64,i64>": ("dict_si", "own"),
}

# rust type per tag, used by the code generator for local declarations
DECL_TYPES = {"list_int": "Vec<i64>", "list_str": "Vec<String>",
              "str_": "String", "bytes_": "Vec<u8>", "int_mag": "i64",
              "list_float": "Vec<f64>", "list_list_int": "Vec<Vec<i64>>",
              "set_int": "std::collections::HashSet<i64>",
              "bool_": "bool",
              "duration_ms": "std::time::Duration",
              "dict_si": "std::collections::HashMap<i64,i64>"}


def _normalize(type_text: str) -> str:
    t = re.sub(r"\s+", "", type_text)
    t = t.replace("&'_", "&").replace("std::collections::", "")
    t = t.replace("std::time::", "").replace("core::time::", "")
    t = re.sub(r"&'[a-zA-Z_]\w*", "&", t)   # one named input lifetime is fine
    return t


def crate_name(cargo_toml: Path) -> str:
    """Raw [package].name (dashes intact — Cargo.toml wants this form;
    use .replace('-', '_') for the code identifier)."""
    text = cargo_toml.read_text()
    in_package = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_package = line == "[package]"
        elif in_package and line.startswith("name"):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"no [package].name in {cargo_toml}")


def discover_crate(crate_root: Path) -> list[dict]:
    """Enumerate drivable pub fns. crate_root contains Cargo.toml; only the
    library target (src/lib.rs) is reachable from an external harness."""
    lib = crate_root / "src" / "lib.rs"
    if not lib.exists():
        raise RuntimeError(
            f"{crate_root} has no src/lib.rs — only library crates can be "
            "measured (an external harness cannot call into a binary)")
    crate = crate_name(crate_root / "Cargo.toml").replace("-", "_")
    functions: list[dict] = []
    _scan_file(lib, crate, [], crate_root / "src", functions, module_pub=True)
    return functions


def _scan_file(path: Path, crate: str, mod_path: list[str], src_root: Path,
               out: list[dict], module_pub: bool) -> None:
    parser = Parser(RUST)
    tree = parser.parse(path.read_bytes())
    _walk(tree.root_node, path, crate, mod_path, src_root, out, module_pub)


def _is_pub(node) -> bool:
    vis = node.child_by_field_name("visibility") or next(
        (c for c in node.children if c.type == "visibility_modifier"), None)
    return vis is not None and vis.text == b"pub"


def _cfg_test(attrs: list) -> bool:
    return any(b"cfg" in a.text and b"test" in a.text for a in attrs)


_HOST = {"unix": True, "windows": False,
         'target_os="linux"': True, 'target_os="windows"': False,
         'target_os="macos"': False,
         'target_family="unix"': True, 'target_family="windows"': False}
if not sys.platform.startswith("linux"):  # pragma: no cover
    _HOST = {}  # only evaluate cfgs on the platform this table describes


def _cfg_inactive(attrs: list) -> str | None:
    """Simple platform cfgs (#[cfg(windows)], #[cfg(target_os = "...")])
    that are OFF on this host. Complex expressions (any/all/not) are not
    evaluated — the compile-retry loop remains their backstop."""
    for a in attrs:
        text = re.sub(r"\s+", "", a.text.decode())
        m = re.fullmatch(r"#\[cfg\(([^()]*)\)\]", text)
        if m and _HOST.get(m.group(1)) is False:
            return m.group(1)
    return None


def _walk(node, path, crate, mod_path, src_root, out, module_pub):
    pending_attrs = []
    for child in node.children:
        if child.type == "attribute_item":
            pending_attrs.append(child)
            continue
        attrs, pending_attrs = pending_attrs, []
        if child.type == "function_item":
            inactive = _cfg_inactive(attrs)
            entry = _describe(child, path, crate, mod_path, module_pub)
            if entry is None:
                continue                # private / unreachable: not API
            if inactive:
                entry.update(drivable=False, params=[],
                             skip_reason=f"cfg_inactive: {inactive}")
            out.append(entry)
        elif child.type == "mod_item":
            if _cfg_test(attrs):        # #[cfg(test)] mod: never reachable
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = name_node.text.decode()
            inactive = _cfg_inactive(attrs)
            if inactive:
                # one honest marker line for the whole platform-gated module
                out.append({"fid": "::".join([crate, *mod_path, name]),
                            "file": str(path),
                            "line": child.start_point[0] + 1, "params": [],
                            "drivable": False,
                            "skip_reason": f"cfg_inactive: {inactive} "
                                           "(module skipped)"})
                continue
            pub = module_pub and _is_pub(child)
            body = child.child_by_field_name("body")
            if body is not None:                      # inline mod { }
                _walk(body, path, crate, mod_path + [name], src_root, out, pub)
            else:                                     # mod file
                for cand in (src_root / Path(*mod_path) / f"{name}.rs",
                             src_root / Path(*mod_path) / name / "mod.rs"):
                    if cand.exists():
                        _scan_file(cand, crate, mod_path + [name], src_root,
                                   out, pub)
                        break


def _describe(node, path, crate, mod_path, module_pub) -> dict | None:
    name = node.child_by_field_name("name").text.decode()
    fid = "::".join([crate, *mod_path, name])
    base = {"fid": fid, "file": str(path),
            "line": node.start_point[0] + 1, "params": []}

    def skip(reason):
        return {**base, "drivable": False, "skip_reason": reason}

    # private functions are not public API: excluded from the report
    # entirely, matching the Python runner's policy for _-prefixed names
    if not _is_pub(node) or not module_pub:
        return None
    if node.child_by_field_name("type_parameters") is not None:
        return skip("generic")

    params = []
    plist = node.child_by_field_name("parameters")
    for p in plist.children if plist else []:
        if p.type == "self_parameter":
            return skip("method (self)")
        if p.type != "parameter":
            continue
        pname_node = p.child_by_field_name("pattern")
        ptype_node = p.child_by_field_name("type")
        if pname_node is None or ptype_node is None or \
                pname_node.type != "identifier":
            return skip("unsupported parameter pattern")
        raw = ptype_node.text.decode()
        if "&mut" in raw.replace(" ", ""):
            return skip(f"param '{pname_node.text.decode()}': &mut")
        norm = _normalize(raw)
        entry = TYPE_WHITELIST.get(norm)
        pinfo = {"name": pname_node.text.decode(),
                 "spec_type": entry[0] if entry else None,
                 "omitted": False,
                 "detail": "" if entry else f"unsupported type {raw!r}",
                 "style": entry[1] if entry else None,
                 "rust_type": norm}
        params.append(pinfo)
    undrivable = [p for p in params if p["spec_type"] is None]
    return {**base, "params": params,
            "drivable": not undrivable,
            "skip_reason": (f"param '{undrivable[0]['name']}': "
                            f"{undrivable[0]['detail']}"
                            if undrivable else None)}
