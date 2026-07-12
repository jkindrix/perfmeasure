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

# normalized type string -> (spec tag, passing style[, cast element type])
# style: "borrow_slice" &v[..] | "borrow" &v | "own" clone per call | "copy"
#        | "borrow_str_slice" &[&str] view | "none" Option params get None
# 3rd element: integer/float slices of other widths are generated as the
# base type and cast element-wise in the arm (untimed)
TYPE_WHITELIST: dict[str, tuple] = {
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
    "u16": ("int_mag", "copy"),
    "i16": ("int_mag", "copy"),
    "u8": ("int_mag", "copy"),
    "i8": ("int_mag", "copy"),
    "&[u64]": ("list_int", "borrow_slice", "u64"),
    "Vec<u64>": ("list_int", "own", "u64"),
    "&[u32]": ("list_int", "borrow_slice", "u32"),
    "Vec<u32>": ("list_int", "own", "u32"),
    "&[usize]": ("list_int", "borrow_slice", "usize"),
    "&[f32]": ("list_float", "borrow_slice", "f32"),
    "Vec<f32>": ("list_float", "own", "f32"),
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
    t = t.replace("std::path::", "")
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
    ctors = Ctors(crate)
    _collect_ctors(lib, crate, [], crate_root / "src", ctors)
    ctors.finalize()
    functions: list[dict] = []
    _scan_file(lib, crate, [], crate_root / "src", functions,
               module_pub=True, ctors=ctors)
    return functions


class Ctors:
    """Construction expressions for same-crate types, collected textually:
    unit structs, #[derive(Default)], a zero-arg `pub fn new()`, or —
    after finalize() — `new(args)` with synthesized arguments (empty
    containers and small scalars type-infer, so most args need no type
    knowledge). Compile-retry backstops what the text gets wrong."""

    def __init__(self, crate: str):
        self.crate = crate
        self.by_path: dict[str, str] = {}     # crate::mod::X -> ctor expr
        self.by_name: dict[str, list[str]] = {}
        # crate::mod::X -> (mod_path, [param norm types], ret norm type)
        self.new_sigs: dict[str, tuple[list[str], list[str], str]] = {}

    def add(self, mod_path: list[str], name: str, expr: str,
            override: bool = False) -> None:
        path = "::".join([self.crate, *mod_path, name])
        if override or path not in self.by_path:
            self.by_path[path] = expr
        self.by_name.setdefault(name, [])
        if path not in self.by_name[name]:
            self.by_name[name].append(path)

    def add_new_sig(self, mod_path: list[str], name: str,
                    param_types: list[str], ret: str) -> None:
        path = "::".join([self.crate, *mod_path, name])
        self.new_sigs.setdefault(path, (mod_path, param_types, ret))
        self.by_name.setdefault(name, [])
        if path not in self.by_name[name]:
            self.by_name[name].append(path)

    def resolve(self, name: str, mod_path: list[str]) -> str | None:
        """Same-module first, else a crate-unique name match."""
        exact = "::".join([self.crate, *mod_path, name])
        if exact in self.by_path:
            return self.by_path[exact]
        paths = self.by_name.get(name, [])
        if len(paths) == 1:
            return self.by_path.get(paths[0])
        return None

    def finalize(self) -> None:
        """Synthesize `Type::new(args)` expressions for types that lack a
        simpler constructor. Recursion bounded by a cycle guard."""
        for path in list(self.new_sigs):
            self._ctor_for(path, set())

    def _ctor_for(self, path: str, seen: set[str]) -> str | None:
        if path in self.by_path:
            return self.by_path[path]
        if path not in self.new_sigs or path in seen:
            return None
        seen = seen | {path}
        mod_path, param_types, ret = self.new_sigs[path]
        name = path.rsplit("::", 1)[1]
        if not (ret in ("Self", name)
                or ret.startswith((f"Result<Self", f"Result<{name}",
                                   f"Option<Self", f"Option<{name}"))):
            return None
        args = []
        for t in param_types:
            expr = self._synth_arg(t, mod_path, seen)
            if expr is None:
                return None
            args.append(expr)
        expr = f"{path}::new({', '.join(args)})"
        if ret.startswith(("Result<", "Option<")):
            expr += ".unwrap()"
        self.by_path[path] = expr
        return expr

    def _synth_arg(self, norm: str, mod_path: list[str],
                   seen: set[str]) -> str | None:
        if norm in ("usize", "u64", "i64", "u32", "i32", "u16", "i16",
                    "u8", "i8"):
            return "1"
        if norm in ("f64", "f32"):
            return "1.0"
        if norm == "bool":
            return "false"
        if norm == "&str":
            return '"x"'
        if norm == "String":
            return '"x".to_string()'
        if norm == "Duration":
            return "std::time::Duration::from_millis(1)"
        if norm == "PathBuf":
            return "std::path::PathBuf::new()"
        if norm == "&Path":
            return 'std::path::Path::new("")'
        if norm.startswith("Option<"):
            return "None"                     # type-infers
        if norm.startswith("Vec<"):
            return "Vec::new()"               # type-infers
        if norm.startswith("&["):
            return "&[]"                      # type-infers
        if norm.startswith("HashMap<"):
            return "std::collections::HashMap::new()"
        if norm.startswith("HashSet<"):
            return "std::collections::HashSet::new()"
        m = re.fullmatch(r"(&?)([A-Z]\w*)", norm)
        if m:                                 # same-crate type: recurse
            inner = self._resolve_or_synth(m.group(2), mod_path, seen)
            if inner is not None:
                return f"&{inner}" if m.group(1) else inner
        return None

    def _resolve_or_synth(self, name: str, mod_path: list[str],
                          seen: set[str]) -> str | None:
        exact = "::".join([self.crate, *mod_path, name])
        got = self._ctor_for(exact, seen)
        if got or exact in self.by_path:
            return self.by_path.get(exact)
        paths = self.by_name.get(name, [])
        if len(paths) == 1:
            return self._ctor_for(paths[0], seen) \
                or self.by_path.get(paths[0])
        return None


def _collect_ctors(path: Path, crate: str, mod_path: list[str],
                   src_root: Path, ctors: Ctors) -> None:
    parser = Parser(RUST)
    _collect_walk(parser.parse(path.read_bytes()).root_node,
                  path, crate, mod_path, src_root, ctors)


def _collect_walk(node, path, crate, mod_path, src_root, ctors):
    pending = []
    for child in node.children:
        if child.type == "attribute_item":
            pending.append(child)
            continue
        attrs, pending = pending, []
        if child.type in ("struct_item", "enum_item"):
            name_node = child.child_by_field_name("name")
            if name_node is None or \
                    child.child_by_field_name("type_parameters") is not None:
                continue
            name = name_node.text.decode()
            full = "::".join([crate, *mod_path, name])
            derives_default = any(
                b"derive" in a.text and b"Default" in a.text for a in attrs)
            is_unit = (child.type == "struct_item"
                       and child.child_by_field_name("body") is None)
            if is_unit:
                ctors.add(mod_path, name, full, override=True)
            elif derives_default:
                ctors.add(mod_path, name, f"{full}::default()", override=True)
        elif child.type == "impl_item":
            if child.child_by_field_name("trait") is not None:
                # `impl Default for X` counts as constructible
                tr = child.child_by_field_name("trait")
                tnode = child.child_by_field_name("type")
                if tr is not None and tr.text == b"Default" \
                        and tnode is not None \
                        and tnode.type == "type_identifier":
                    name = tnode.text.decode()
                    full = "::".join([crate, *mod_path, name])
                    ctors.add(mod_path, name, f"{full}::default()",
                              override=True)
                continue
            tnode = child.child_by_field_name("type")
            body = child.child_by_field_name("body")
            if tnode is None or tnode.type != "type_identifier" or body is None:
                continue
            name = tnode.text.decode()
            for member in body.children:
                if member.type != "function_item":
                    continue
                fname = member.child_by_field_name("name")
                if fname is None or fname.text != b"new" \
                        or not _is_pub(member) \
                        or member.child_by_field_name("type_parameters"):
                    continue
                plist = member.child_by_field_name("parameters")
                pnodes = [p for p in (plist.children if plist else [])
                          if p.type in ("parameter", "self_parameter")]
                if any(p.type == "self_parameter" for p in pnodes):
                    continue                 # takes self: not a constructor
                if not pnodes:
                    full = "::".join([crate, *mod_path, name])
                    ctors.add(mod_path, name, f"{full}::new()")
                    continue
                ptypes = []
                for p in pnodes:
                    tn = p.child_by_field_name("type")
                    ptypes.append(_normalize(tn.text.decode()) if tn else "?")
                rnode = member.child_by_field_name("return_type")
                ret = _normalize(rnode.text.decode()) if rnode else "?"
                ctors.add_new_sig(mod_path, name, ptypes, ret)
        elif child.type == "mod_item":
            name_node = child.child_by_field_name("name")
            if name_node is None or _cfg_test(attrs) or _cfg_inactive(attrs):
                continue
            name = name_node.text.decode()
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_walk(body, path, crate, mod_path + [name],
                              src_root, ctors)
            else:
                for cand in (src_root / Path(*mod_path) / f"{name}.rs",
                             src_root / Path(*mod_path) / name / "mod.rs"):
                    if cand.exists():
                        _collect_ctors(cand, crate, mod_path + [name],
                                       src_root, ctors)
                        break


def _scan_file(path: Path, crate: str, mod_path: list[str], src_root: Path,
               out: list[dict], module_pub: bool, ctors: Ctors) -> None:
    parser = Parser(RUST)
    tree = parser.parse(path.read_bytes())
    _walk(tree.root_node, path, crate, mod_path, src_root, out, module_pub,
          ctors)


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


def _walk(node, path, crate, mod_path, src_root, out, module_pub, ctors):
    pending_attrs = []
    for child in node.children:
        if child.type == "attribute_item":
            pending_attrs.append(child)
            continue
        attrs, pending_attrs = pending_attrs, []
        if child.type == "function_item":
            inactive = _cfg_inactive(attrs)
            entry = _describe(child, path, crate, mod_path, mod_path,
                              module_pub, ctors)
            if entry is None:
                continue                # private / unreachable: not API
            if inactive:
                entry.update(drivable=False, params=[],
                             skip_reason=f"cfg_inactive: {inactive}")
            out.append(entry)
        elif child.type == "impl_item":
            # inherent impls only: associated fns are callable as Type::fn;
            # trait impls and generic impls stay out (semantic-model trap)
            if child.child_by_field_name("trait") is not None \
                    or child.child_by_field_name("type_parameters") is not None:
                continue
            tnode = child.child_by_field_name("type")
            body = child.child_by_field_name("body")
            if tnode is None or tnode.type != "type_identifier" or body is None:
                continue
            impl_path = mod_path + [tnode.text.decode()]
            impl_attrs = []
            for member in body.children:
                if member.type == "attribute_item":
                    impl_attrs.append(member)
                    continue
                mattrs, impl_attrs = impl_attrs, []
                if member.type != "function_item":
                    continue
                entry = _describe(member, path, crate, impl_path, mod_path,
                                  module_pub, ctors)
                if entry is None:
                    continue
                inactive = _cfg_inactive(mattrs)
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
                _walk(body, path, crate, mod_path + [name], src_root, out,
                      pub, ctors)
            else:                                     # mod file
                for cand in (src_root / Path(*mod_path) / f"{name}.rs",
                             src_root / Path(*mod_path) / name / "mod.rs"):
                    if cand.exists():
                        _scan_file(cand, crate, mod_path + [name], src_root,
                                   out, pub, ctors)
                        break


def _describe(node, path, crate, fid_path, module_ctx, module_pub,
              ctors: Ctors) -> dict | None:
    name = node.child_by_field_name("name").text.decode()
    fid = "::".join([crate, *fid_path, name])
    base = {"fid": fid, "file": str(path),
            "line": node.start_point[0] + 1, "params": []}

    def skip(reason):
        return {**base, "drivable": False, "skip_reason": reason}

    # private functions are not public API: excluded from the report
    # entirely, matching the Python runner's policy for _-prefixed names
    if not _is_pub(node) or not module_pub:
        return None
    if any(c.type == "function_modifiers" and b"async" in c.text
           for c in node.children):
        # calling an async fn only constructs the future; timing that
        # would be a silent lie about the actual work
        return skip("async fn (harness does not execute futures)")
    if node.child_by_field_name("type_parameters") is not None:
        return skip("generic")

    params = []
    receiver = None
    plist = node.child_by_field_name("parameters")
    for p in plist.children if plist else []:
        if p.type == "self_parameter":
            self_text = " ".join(p.text.decode().split())
            if self_text != "&self":
                return skip(f"method ({self_text} receiver — mutation/"
                            "consumption not repeatable)")
            type_name = fid_path[-1] if fid_path != module_ctx else None
            ctor = (ctors.resolve(type_name, module_ctx)
                    if type_name else None)
            if ctor is None:
                return skip("method (self receiver; no synthesizable "
                            f"constructor for {type_name})")
            receiver = ctor
            continue
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
        type_ref = None
        if entry is None and norm.startswith("Option<"):
            entry = ("opt_none", "none")     # None type-infers at any call site
        if entry is None:
            # same-crate constructible struct: a fixed default instance
            m = re.fullmatch(r"(&?)([A-Z]\w*)", norm)
            if m:
                ctor = ctors.resolve(m.group(2), module_ctx)
                if ctor is not None:
                    entry = ("instance_",
                             "borrow_ctor" if m.group(1) else "own_ctor")
                    type_ref = ctor
        detail = ""
        if entry is None:
            detail = (f"filesystem path (I/O domain, not generated)"
                      if norm in ("&Path", "PathBuf", "&PathBuf")
                      else f"unsupported type {raw!r}")
        pinfo = {"name": pname_node.text.decode(),
                 "spec_type": entry[0] if entry else None,
                 "omitted": False,
                 "detail": detail,
                 "style": entry[1] if entry else None,
                 "cast": entry[2] if entry and len(entry) > 2 else None,
                 "type_ref": type_ref,
                 "rust_type": norm}
        params.append(pinfo)
    undrivable = [p for p in params if p["spec_type"] is None]
    result = {**base, "params": params,
              "drivable": not undrivable,
              "skip_reason": (f"param '{undrivable[0]['name']}': "
                              f"{undrivable[0]['detail']}"
                              if undrivable else None)}
    if receiver is not None:
        result["receiver"] = receiver
    return result
