from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node as TSNode, Parser

from perf_lint.ir import CONST, GROWN, Call, Function, Loop, Node, Op

PY_LANGUAGE = Language(tspython.language())

COMPREHENSIONS = {
    "list_comprehension",
    "set_comprehension",
    "dictionary_comprehension",
    "generator_expression",
}
LITERALS = {
    "list", "tuple", "set", "dictionary",
    "string", "integer", "float", "true", "false", "none",
}
# Wrappers whose iteration count equals their first argument's size.
ITER_WRAPPERS = {"enumerate", "reversed", "sorted", "list", "set", "tuple", "iter"}
DICT_VIEWS = {"keys", "values", "items"}

# local type inference: constructor/literal → kind
_CTOR_KINDS = {"list": "list", "set": "set", "dict": "dict", "str": "str", "sorted": "list"}
_COMP_KINDS = {
    "list_comprehension": "list",
    "set_comprehension": "set",
    "dictionary_comprehension": "dict",
}
# usage-based: method call → receiver kind
_USAGE_KINDS = {
    "append": "list", "insert": "list", "extend": "list", "sort": "list",
    "add": "set", "discard": "set",
    "keys": "dict", "values": "dict", "items": "dict", "setdefault": "dict",
}
_GROW_METHODS = {"append", "insert", "extend", "add"}
# list methods with table costs
_COSTED_METHODS = {"index", "count", "remove", "insert", "pop", "sort"}


class PythonAdapter:
    language = "python"
    extensions = (".py",)

    def parse(self, path: str, source: bytes) -> list[Function]:
        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(source)
        functions: list[Function] = []
        self._find_functions(tree.root_node, path, functions)
        return functions

    # -- function discovery ---------------------------------------------------

    def _find_functions(self, node: TSNode, path: str, out: list[Function]) -> None:
        for child in node.children:
            if child.type == "function_definition":
                name = self._text(child.child_by_field_name("name"))
                fn = Function(
                    name=name, file=path, line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    params=self._param_names(child.child_by_field_name("parameters")),
                )
                body = child.child_by_field_name("body")
                (
                    self._kinds, self._grown, self._empty_init, self._aliases
                ) = self._infer_context(body)
                fn.body = self._collect(body)
                out.append(fn)
                self._find_functions(body, path, out)
            else:
                self._find_functions(child, path, out)

    def _param_names(self, node: TSNode | None) -> list[str]:
        if node is None:
            return []
        names: list[str] = []
        for child in node.named_children:
            if child.type == "identifier":
                names.append(self._text(child))
            elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                ident = child.child_by_field_name("name") or next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                if ident is None:
                    break
                names.append(self._text(ident))
            else:
                break  # *args / keyword-only / ** — positional matching stops here
        if names and names[0] in ("self", "cls"):
            names = names[1:]
        return names

    # -- local kind/growth inference -------------------------------------------

    def _infer_context(
        self, body: TSNode
    ) -> tuple[dict[str, str], set[str], set[str], dict[str, str]]:
        kinds: dict[str, str] = {}
        grown: set[str] = set()
        empty_init: set[str] = set()
        alias_of: dict[str, str] = {}  # name -> bare identifier it was set to
        ambiguous: set[str] = set()  # reassigned or set to a non-identifier

        def walk(node: TSNode) -> None:
            if node.type == "function_definition":
                return
            if node.type == "assignment":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                if left is not None and right is not None and left.type == "identifier":
                    name = self._text(left)
                    if right.type == "identifier" and name not in ambiguous:
                        target = self._text(right)
                        if name in alias_of and alias_of[name] != target:
                            ambiguous.add(name)  # aliased to two different names
                        else:
                            alias_of[name] = target
                    else:
                        ambiguous.add(name)  # assigned to a non-identifier
                    kind = self._expr_kind(right, kinds)
                    if kind != "unknown":
                        kinds[name] = kind
                    if self._is_empty_literal(right):
                        empty_init.add(name)
                    else:
                        empty_init.discard(name)
            elif node.type == "augmented_assignment":
                left = node.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    name = self._text(left)
                    if kinds.get(name) == "list":
                        grown.add(name)
            elif node.type == "call":
                fn = node.child_by_field_name("function")
                if fn is not None and fn.type == "attribute":
                    obj = fn.child_by_field_name("object")
                    attr = self._text(fn.child_by_field_name("attribute"))
                    if obj is not None and obj.type == "identifier":
                        name = self._text(obj)
                        if attr in _USAGE_KINDS:
                            kinds.setdefault(name, _USAGE_KINDS[attr])
                        if attr in _GROW_METHODS:
                            grown.add(name)
            for child in node.children:
                walk(child)

        walk(body)

        def resolve(name: str, seen: frozenset[str]) -> str:
            target = alias_of.get(name)
            if target is None or name in ambiguous or target in seen:
                return name
            return resolve(target, seen | {name})

        aliases = {
            n: resolve(n, frozenset())
            for n in alias_of
            if n not in ambiguous and resolve(n, frozenset()) != n
        }
        return kinds, grown, empty_init, aliases

    def _is_empty_literal(self, node: TSNode) -> bool:
        t = node.type
        if t in ("list", "set", "dictionary") and not node.named_children:
            return True
        if t == "string" and len(node.text or b"") <= 2:
            return True
        if t == "call":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            return (
                fn is not None and fn.type == "identifier"
                and self._text(fn) in ("list", "set", "dict")
                and (args is None or not args.named_children)
            )
        return False

    def _expr_kind(self, node: TSNode, kinds: dict[str, str]) -> str:
        t = node.type
        if t == "identifier":
            return kinds.get(self._text(node), "unknown")
        if t == "list":
            return "list"
        if t == "set":
            return "set"
        if t == "dictionary":
            return "dict"
        if t == "string" or t == "concatenated_string":
            return "str"
        if t == "tuple":
            return "tuple"
        if t in _COMP_KINDS:
            return _COMP_KINDS[t]
        if t == "call":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                return _CTOR_KINDS.get(self._text(fn), "unknown")
            if fn is not None and fn.type == "attribute":
                attr = self._text(fn.child_by_field_name("attribute"))
                if attr in ("keys", "items"):
                    return "dict"  # set-like views: O(1) membership
                if attr == "values":
                    return "list"  # values view: linear membership
        return "unknown"

    # -- body walking -----------------------------------------------------------

    def _collect(self, node: TSNode) -> list[Node]:
        out: list[Node] = []
        self._visit(node, out)
        return out

    def _visit(self, node: TSNode, out: list[Node]) -> None:
        t = node.type
        if t == "function_definition":
            return  # nested defs are separate functions
        if t == "for_statement":
            right = node.child_by_field_name("right")
            body = self._collect(node.child_by_field_name("body"))
            targets = self._names(node.child_by_field_name("left"))
            if right.type in COMPREHENSIONS:
                # `for x in (genexp)` iterates the genexp's clauses directly;
                # modelling both the for and the clauses would double-count
                chain = self._comprehension_chain(right, tail=body, out=out)
                for loop in self._iter_loops(chain):
                    loop.target_names = list(set(loop.target_names) | set(targets))
                out.extend(chain)
            else:
                self._visit(right, out)  # header calls run once, outside the loop
                loop = self._make_loop("for", right, node)
                loop.target_names = targets
                loop.body = body
                out.append(loop)
        elif t == "while_statement":
            loop = Loop(
                kind="while", size_symbol=None,
                display=self._text(node.child_by_field_name("condition")),
                root_name=None, target_names=[],
                line=node.start_point[0] + 1,
            )
            # the condition is evaluated every iteration
            loop.body = self._collect(node.child_by_field_name("condition"))
            loop.body += self._collect(node.child_by_field_name("body"))
            out.append(loop)
        elif t in COMPREHENSIONS:
            out.extend(self._comprehension_chain(node, tail=[], out=out))
        elif t == "comparison_operator":
            if any(c.type in ("in", "not in") for c in node.children) and len(node.named_children) >= 2:
                recv = node.named_children[-1]
                out.append(self._make_op("contains", recv, node))
            for child in node.children:
                self._visit(child, out)
        elif t == "augmented_assignment":
            left = node.child_by_field_name("left")
            op_tok = next((c for c in node.children if c.type == "+="), None)
            if (
                op_tok is not None and left is not None and left.type == "identifier"
                and self._kinds.get(self._text(left)) == "str"
            ):
                out.append(Op(
                    kind="str_concat", recv_kind="str", recv_sym=GROWN,
                    recv_display=self._text(left), display=self._text(node),
                    line=node.start_point[0] + 1,
                ))
            self._visit(node.child_by_field_name("right"), out)
        elif t == "call":
            fn_node = node.child_by_field_name("function")
            handled = False
            if fn_node.type == "attribute":
                attr = self._text(fn_node.child_by_field_name("attribute"))
                obj = fn_node.child_by_field_name("object")
                if attr in _COSTED_METHODS and obj is not None:
                    args = self._pos_args(node)
                    if attr != "pop" or (args and self._text(args[0]) != "-1"):
                        out.append(self._make_op(f"method:{attr}", obj, node))
                        handled = True
            elif fn_node.type == "identifier" and self._text(fn_node) == "sorted":
                args = self._pos_args(node)
                if args:
                    out.append(self._make_op("function:sorted", args[0], node, recv_kind="any"))
                    handled = True
            if not handled and fn_node.type in ("identifier", "attribute"):
                args = self._pos_args(node)
                syms, displays = [], []
                for a in args:
                    sym, display, _root = self._size_symbol(a)
                    syms.append(sym)
                    displays.append(display)
                out.append(Call(
                    callee=self._text(fn_node), line=node.start_point[0] + 1,
                    arg_syms=syms, arg_displays=displays,
                ))
            for child in node.children:
                self._visit(child, out)
        else:
            for child in node.children:
                self._visit(child, out)

    def _comprehension_chain(self, node: TSNode, tail: list[Node], out: list[Node]) -> list[Node]:
        clauses = [c for c in node.named_children if c.type == "for_in_clause"]
        rest: list[Node] = []
        for c in node.named_children:
            if c.type != "for_in_clause":
                self._visit(c, rest)
        inner: list[Node] = rest + tail
        for clause in reversed(clauses):
            right = clause.child_by_field_name("right")
            loop = self._make_loop("comprehension", right, clause)
            loop.target_names = self._names(clause.child_by_field_name("left"))
            loop.body = inner
            inner = [loop]
        if clauses:  # outermost header is evaluated once, outside the loops
            self._visit(clauses[0].child_by_field_name("right"), out)
        return inner

    def _iter_loops(self, nodes: list[Node]):
        for n in nodes:
            if isinstance(n, Loop):
                yield n
                yield from self._iter_loops(n.body)

    def _pos_args(self, call: TSNode) -> list[TSNode]:
        args = call.child_by_field_name("arguments")
        if args is None:
            return []
        return [a for a in args.named_children if a.type != "keyword_argument"]

    def _make_loop(self, kind: str, iterated: TSNode, at: TSNode) -> Loop:
        sym, display, root = self._size_symbol(iterated)
        return Loop(
            kind=kind, size_symbol=sym, display=display, root_name=root,
            target_names=[], line=at.start_point[0] + 1,
        )

    def _make_op(self, kind: str, recv: TSNode, at: TSNode, recv_kind: str | None = None) -> Op:
        sym, recv_display, _root = self._size_symbol(recv)
        if (
            recv.type == "identifier"
            and self._text(recv) in self._grown
            and self._text(recv) in self._empty_init
        ):
            sym = GROWN
        return Op(
            kind=kind,
            recv_kind=recv_kind or self._expr_kind(recv, self._kinds),
            recv_sym=sym, recv_display=recv_display,
            display=self._line_snippet(at), line=at.start_point[0] + 1,
        )

    def _line_snippet(self, node: TSNode) -> str:
        text = self._text(node)
        return text if len(text) <= 60 else text[:57] + "..."

    # -- size symbols -------------------------------------------------------------

    def _size_symbol(self, node: TSNode) -> tuple[str | None, str, str | None]:
        """Map an expression to (size symbol, display, root_name).

        Identical symbols mean "same size"; equating by normalized source text
        is the documented syntactic approximation for v1.
        """
        t = node.type
        text = self._text(node)
        if t == "parenthesized_expression":
            for c in node.named_children:
                return self._size_symbol(c)
        if t == "identifier":
            # canonicalize simple aliases (`ys = xs`) so loops over xs and ys
            # count as the same collection; display keeps the written name
            canon = self._aliases.get(text, text)
            return f"size:{canon}", text, canon
        if t == "attribute":
            return f"size:{text}", text, self._root_name(node)
        if t in LITERALS:
            return CONST, text, None
        if t == "call":
            fn = node.child_by_field_name("function")
            args = self._pos_args(node)
            if fn.type == "identifier":
                name = self._text(fn)
                if name == "range":
                    if args and all(a.type == "integer" for a in args):
                        return CONST, text, None
                    stop = args[1] if len(args) >= 2 else (args[0] if args else None)
                    if stop is not None:
                        return self._size_symbol(stop)
                if name == "len" and args:
                    return self._size_symbol(args[0])
                if name in ITER_WRAPPERS and args:
                    return self._size_symbol(args[0])
            if (
                fn.type == "attribute"
                and not args
                and self._text(fn.child_by_field_name("attribute")) in DICT_VIEWS
            ):
                return self._size_symbol(fn.child_by_field_name("object"))
        # fallback: equate by source text
        return f"size:{text}", text, self._root_name(node)

    # -- helpers ---------------------------------------------------------------------

    def _root_name(self, node: TSNode) -> str | None:
        while node is not None:
            if node.type == "identifier":
                return self._text(node)
            if node.type == "attribute":
                node = node.child_by_field_name("object")
            elif node.type == "call":
                node = node.child_by_field_name("function")
            elif node.type == "subscript":
                node = node.child_by_field_name("value")
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
