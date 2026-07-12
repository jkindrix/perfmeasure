from __future__ import annotations

import tree_sitter_rust as tsrust
from tree_sitter import Language, Node as TSNode, Parser

from perf_lint.ir import CONST, GROWN, Call, Function, Loop, Node, Op

RUST_LANGUAGE = Language(tsrust.language())

# iterator adapters/consumers: a method chain containing any of these is a
# single lazily-fused pass over the chain's root receiver
ITER_METHODS = {
    "iter", "into_iter", "iter_mut", "chars", "bytes", "lines",
    "split", "split_whitespace", "keys", "values",
    "map", "filter", "filter_map", "flat_map", "for_each", "fold",
    "any", "all", "find", "position", "count", "sum", "product",
    "collect", "enumerate", "zip", "chain", "rev",
    "take_while", "skip_while", "inspect",
    "max_by", "min_by", "max_by_key", "min_by_key",
}
# single-pass wrappers that preserve the receiver's size symbol
SIZE_WRAPPERS = {"iter", "into_iter", "iter_mut", "keys", "values", "clone", "len"}
COSTED_METHODS = {
    "contains", "insert", "remove",
    "sort", "sort_unstable", "sort_by", "sort_by_key", "sort_unstable_by",
}
_SORTS = {"sort", "sort_unstable", "sort_by", "sort_by_key", "sort_unstable_by"}

_TYPE_KINDS = [
    ("Vec", "vec"), ("VecDeque", "vec"), ("&[", "vec"), ("[", "vec"),
    ("HashSet", "set"), ("BTreeSet", "set"),
    ("HashMap", "map"), ("BTreeMap", "map"),
    ("String", "str"), ("str", "str"),
]
_CTOR_KINDS = {"Vec": "vec", "VecDeque": "vec", "HashSet": "set",
               "BTreeSet": "set", "HashMap": "map", "BTreeMap": "map",
               "String": "str"}
_GROW_METHODS = {"push", "insert", "extend", "append", "push_str"}


class RustAdapter:
    language = "rust"
    extensions = (".rs",)

    def parse(self, path: str, source: bytes) -> list[Function]:
        parser = Parser(RUST_LANGUAGE)
        tree = parser.parse(source)
        functions: list[Function] = []
        self._find_functions(tree.root_node, path, functions)
        return functions

    # -- function discovery ---------------------------------------------------

    def _find_functions(self, node: TSNode, path: str, out: list[Function]) -> None:
        for child in node.children:
            if child.type == "function_item":
                name = self._text(child.child_by_field_name("name"))
                params, param_kinds = self._params(child.child_by_field_name("parameters"))
                fn = Function(
                    name=name, file=path, line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1, params=params,
                )
                body = child.child_by_field_name("body")
                if body is not None:
                    self._kinds, self._grown, self._empty_init = self._infer_context(body)
                    self._kinds.update(param_kinds)
                    fn.body = self._collect(body)
                out.append(fn)
                if body is not None:
                    self._find_functions(body, path, out)
            else:
                self._find_functions(child, path, out)

    def _params(self, node: TSNode | None) -> tuple[list[str], dict[str, str]]:
        names: list[str] = []
        kinds: dict[str, str] = {}
        if node is None:
            return names, kinds
        for child in node.named_children:
            if child.type != "parameter":
                continue  # self_parameter etc.
            pattern = child.child_by_field_name("pattern")
            if pattern is None or pattern.type != "identifier":
                break  # positional matching stops at destructuring
            name = self._text(pattern)
            names.append(name)
            kind = self._type_kind(child.child_by_field_name("type"))
            if kind:
                kinds[name] = kind
        return names, kinds

    def _type_kind(self, node: TSNode | None) -> str | None:
        if node is None:
            return None
        text = self._text(node).lstrip("&").removeprefix("mut ").strip()
        for prefix, kind in _TYPE_KINDS:
            if text.startswith(prefix):
                return kind
        return None

    # -- local kind/growth inference --------------------------------------------

    def _infer_context(self, body: TSNode) -> tuple[dict[str, str], set[str], set[str]]:
        kinds: dict[str, str] = {}
        grown: set[str] = set()
        empty_init: set[str] = set()

        def walk(node: TSNode) -> None:
            if node.type == "function_item":
                return
            if node.type == "let_declaration":
                pattern = node.child_by_field_name("pattern")
                value = node.child_by_field_name("value")
                if pattern is not None and pattern.type == "identifier":
                    name = self._text(pattern)
                    kind = self._type_kind(node.child_by_field_name("type")) or (
                        self._value_kind(value) if value is not None else None
                    )
                    if kind:
                        kinds[name] = kind
                    if value is not None and self._is_empty_init(value):
                        empty_init.add(name)
            elif node.type == "call_expression":
                fn = self._unwrap(node.child_by_field_name("function"))
                if fn is not None and fn.type == "field_expression":
                    obj = fn.child_by_field_name("value")
                    method = self._text(fn.child_by_field_name("field"))
                    if obj is not None and obj.type == "identifier" and method in _GROW_METHODS:
                        grown.add(self._text(obj))
            for child in node.children:
                walk(child)

        walk(body)
        return kinds, grown, empty_init

    def _value_kind(self, node: TSNode) -> str | None:
        t = node.type
        text = self._text(node)
        if t == "macro_invocation":
            macro = self._text(node.child_by_field_name("macro"))
            if macro == "vec":
                return "vec"
            if macro == "format":
                return "str"
            return None
        if t == "call_expression":
            fn = self._unwrap(node.child_by_field_name("function"))
            if fn is not None and fn.type == "scoped_identifier":
                ctor = self._text(fn).split("::")[0]
                return _CTOR_KINDS.get(ctor)
            if fn is not None and fn.type == "field_expression":
                method = self._text(fn.child_by_field_name("field"))
                if method == "to_vec":
                    return "vec"
                if method in ("to_string", "to_owned") and "String" in text:
                    return "str"
                if method == "collect":
                    return "vec" if "Vec" in self._text(node) else None
        if t == "string_literal":
            return "str"
        return None

    def _is_empty_init(self, node: TSNode) -> bool:
        text = self._text(node)
        if node.type == "macro_invocation" and text.replace(" ", "") in ("vec![]", "vec!()"):
            return True
        return text.endswith("::new()") or "::with_capacity(" in text

    # -- body walking --------------------------------------------------------------

    def _collect(self, node: TSNode) -> list[Node]:
        out: list[Node] = []
        self._visit(node, out)
        return out

    def _visit(self, node: TSNode, out: list[Node]) -> None:
        t = node.type
        if t == "function_item":
            return  # nested fns are separate functions
        if t == "for_expression":
            value = node.child_by_field_name("value")
            body = node.child_by_field_name("body")
            targets = self._names(node.child_by_field_name("pattern"))
            chain = self._chain_parts(value)
            if chain is not None:
                root, closures, siblings = chain
                sym, display, root_name = self._size_symbol(root)
                self._visit_non_chain(root, out)
                for s in siblings:
                    self._visit(s, out)
                loop_body: list[Node] = []
                for c in closures:
                    self._visit(c, loop_body)
                loop_body += self._collect(body)
            else:
                sym, display, root_name = self._size_symbol(value)
                self._visit(value, out)  # header evaluated once
                loop_body = self._collect(body)
            out.append(Loop(
                kind="for", size_symbol=sym, display=display, root_name=root_name,
                target_names=targets, line=node.start_point[0] + 1, body=loop_body,
            ))
        elif t in ("while_expression", "loop_expression"):
            condition = node.child_by_field_name("condition")
            loop = Loop(
                kind="while", size_symbol=None,
                display=self._text(condition) if condition is not None else "loop",
                root_name=None, target_names=[],
                line=node.start_point[0] + 1,
            )
            if condition is not None:
                loop.body = self._collect(condition)
            loop.body += self._collect(node.child_by_field_name("body"))
            out.append(loop)
        elif t == "call_expression":
            fn = self._unwrap(node.child_by_field_name("function"))
            if fn is None:
                return
            if fn.type == "field_expression":
                method = self._text(fn.child_by_field_name("field"))
                obj = fn.child_by_field_name("value")
                if method in ("next", "nth", "next_back"):
                    chain = self._chain_parts(obj)
                    if chain is not None:
                        # a chain consumed by next()/nth() advances a constant
                        # number of elements — O(1), not a pass over the root
                        root, closures, siblings = chain
                        self._visit_non_chain(root, out)
                        for s in siblings:
                            self._visit(s, out)
                        for c in closures:
                            self._visit(c, out)
                        return
                if method in ITER_METHODS:
                    self._visit_chain(node, out)
                    return
                if method in COSTED_METHODS:
                    kind = f"method:{'sort' if method in _SORTS else method}"
                    out.append(self._make_op(kind, obj, node))
                else:
                    out.append(Call(
                        callee=self._text(fn).replace("::", "."),
                        line=node.start_point[0] + 1,
                        **self._call_args(node),
                    ))
            elif fn.type in ("identifier", "scoped_identifier"):
                out.append(Call(
                    callee=self._text(fn).replace("::", "."),
                    line=node.start_point[0] + 1,
                    **self._call_args(node),
                ))
            for child in node.children:
                self._visit(child, out)
        else:
            for child in node.children:
                self._visit(child, out)

    def _visit_chain(self, node: TSNode, out: list[Node]) -> None:
        """Model an iterator chain as one loop over its root receiver."""
        chain = self._chain_parts(node)
        if chain is None:  # shouldn't happen; fall back to generic recursion
            for child in node.children:
                self._visit(child, out)
            return
        root, closures, siblings = chain
        sym, display, root_name = self._size_symbol(root)
        self._visit_non_chain(root, out)  # calls in the root run once
        for s in siblings:
            self._visit(s, out)
        body: list[Node] = []
        for c in closures:
            self._visit(c, body)
        out.append(Loop(
            kind="chain", size_symbol=sym, display=display, root_name=root_name,
            target_names=self._closure_params(closures),
            line=node.start_point[0] + 1, body=body,
        ))

    def _chain_parts(
        self, node: TSNode | None
    ) -> tuple[TSNode, list[TSNode], list[TSNode]] | None:
        """If node is an iterator chain, return (root, closures, siblings).

        Closures run once per element (loop body). Non-closure args — the
        iterables fed to zip()/chain(), fold's init — are evaluated in lockstep
        or once, NOT per element, so they are siblings of the loop.
        """
        if node is None or node.type != "call_expression":
            return None
        closures: list[TSNode] = []
        siblings: list[TSNode] = []
        current = node
        saw_iter = False
        while current.type == "call_expression":
            fn = self._unwrap(current.child_by_field_name("function"))
            if fn is None or fn.type != "field_expression":
                break
            method = self._text(fn.child_by_field_name("field"))
            if method not in ITER_METHODS:
                break
            saw_iter = True
            args = current.child_by_field_name("arguments")
            for a in (args.named_children if args is not None else []):
                (closures if a.type == "closure_expression" else siblings).append(a)
            current = fn.child_by_field_name("value")
        return (current, list(reversed(closures)), siblings) if saw_iter else None

    def _visit_non_chain(self, node: TSNode, out: list[Node]) -> None:
        # visit a chain root for once-run calls without re-triggering chain logic
        if node.type == "call_expression":
            fn = self._unwrap(node.child_by_field_name("function"))
            if fn is not None and fn.type in ("identifier", "scoped_identifier", "field_expression"):
                self._visit(node, out)

    def _closure_params(self, closures: list[TSNode]) -> list[str]:
        names: list[str] = []
        for c in closures:
            if c.type == "closure_expression":
                names.extend(self._names(c.child_by_field_name("parameters")))
        return names

    def _call_args(self, node: TSNode) -> dict:
        args = node.child_by_field_name("arguments")
        syms, displays = [], []
        for a in (args.named_children if args is not None else []):
            sym, display, _root = self._size_symbol(a)
            syms.append(sym)
            displays.append(display)
        return {"arg_syms": syms, "arg_displays": displays}

    def _make_op(self, kind: str, recv: TSNode | None, at: TSNode) -> Op:
        if recv is None:
            return Op(kind=kind, recv_kind="unknown", recv_sym=None,
                      recv_display="", display=self._line_snippet(at),
                      line=at.start_point[0] + 1)
        sym, recv_display, _root = self._size_symbol(recv)
        recv_kind = "unknown"
        if recv.type == "identifier":
            name = self._text(recv)
            recv_kind = self._kinds.get(name, "unknown")
            if name in self._grown and name in self._empty_init:
                sym = GROWN
        return Op(
            kind=kind, recv_kind=recv_kind, recv_sym=sym,
            recv_display=recv_display, display=self._line_snippet(at),
            line=at.start_point[0] + 1,
        )

    def _line_snippet(self, node: TSNode) -> str:
        text = self._text(node)
        return text if len(text) <= 60 else text[:57] + "..."

    # -- size symbols -------------------------------------------------------------

    def _size_symbol(self, node: TSNode) -> tuple[str | None, str, str | None]:
        t = node.type
        text = self._text(node)
        if t == "parenthesized_expression" and node.named_children:
            return self._size_symbol(node.named_children[0])
        if t == "reference_expression":
            value = node.child_by_field_name("value")
            if value is not None:
                return self._size_symbol(value)
        if t == "identifier":
            return f"size:{text}", text, text
        if t == "field_expression":
            return f"size:{text}", text, self._root_name(node)
        if t in ("integer_literal", "string_literal", "array_expression",
                 "tuple_expression"):
            return CONST, text, None
        if t == "range_expression":
            ends = node.named_children
            if ends and all(e.type == "integer_literal" for e in ends):
                return CONST, text, None
            if ends:
                return self._size_symbol(ends[-1])
        if t == "call_expression":
            fn = self._unwrap(node.child_by_field_name("function"))
            if fn is not None and fn.type == "field_expression":
                method = self._text(fn.child_by_field_name("field"))
                if method in SIZE_WRAPPERS:
                    return self._size_symbol(fn.child_by_field_name("value"))
        return f"size:{text}", text, self._root_name(node)

    # -- helpers ---------------------------------------------------------------------

    def _unwrap(self, node: TSNode | None) -> TSNode | None:
        # collect::<Vec<_>>() wraps the callee in generic_function
        if node is not None and node.type == "generic_function":
            return node.child_by_field_name("function")
        return node

    def _root_name(self, node: TSNode | None) -> str | None:
        while node is not None:
            if node.type == "identifier":
                return self._text(node)
            if node.type == "field_expression":
                node = node.child_by_field_name("value")
            elif node.type == "call_expression":
                node = self._unwrap(node.child_by_field_name("function"))
            elif node.type == "reference_expression":
                node = node.child_by_field_name("value")
            elif node.type == "index_expression" and node.named_children:
                node = node.named_children[0]
            elif node.type == "parenthesized_expression" and node.named_children:
                node = node.named_children[0]
            else:
                return None
        return None

    def _names(self, node: TSNode | None) -> list[str]:
        if node is None:
            return []
        if node.type == "identifier":
            return [self._text(node)]
        out: list[str] = []
        for child in node.named_children:
            out.extend(self._names(child))
        return out

    def _text(self, node: TSNode | None) -> str:
        return node.text.decode("utf8", errors="replace") if node is not None else ""
