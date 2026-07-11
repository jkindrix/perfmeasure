from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node as TSNode, Parser

from perf_lint.ir import CONST, Call, Function, Loop, Node

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


class PythonAdapter:
    extensions = (".py",)

    def parse(self, path: str, source: bytes) -> list[Function]:
        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(source)
        functions: list[Function] = []
        self._find_functions(tree.root_node, path, functions)
        return functions

    # -- function discovery -------------------------------------------------

    def _find_functions(self, node: TSNode, path: str, out: list[Function]) -> None:
        for child in node.children:
            if child.type == "function_definition":
                name = self._text(child.child_by_field_name("name"))
                fn = Function(name=name, file=path, line=child.start_point[0] + 1)
                body = child.child_by_field_name("body")
                fn.body = self._collect(body)
                out.append(fn)
                self._find_functions(body, path, out)
            else:
                self._find_functions(child, path, out)

    # -- body walking -------------------------------------------------------

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
            self._visit(right, out)  # calls in the header run once, outside the loop
            loop = self._make_loop("for", right, node)
            loop.target_names = self._names(node.child_by_field_name("left"))
            loop.body = self._collect(node.child_by_field_name("body"))
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
            clauses = [c for c in node.named_children if c.type == "for_in_clause"]
            rest: list[Node] = []
            for c in node.named_children:
                if c.type != "for_in_clause":
                    self._visit(c, rest)
            inner: list[Node] = rest
            for clause in reversed(clauses):
                right = clause.child_by_field_name("right")
                loop = self._make_loop("comprehension", right, clause)
                loop.target_names = self._names(clause.child_by_field_name("left"))
                loop.body = inner
                inner = [loop]
            # header of the outermost clause is evaluated once
            if clauses:
                self._visit(clauses[0].child_by_field_name("right"), out)
            out.extend(inner)
        elif t == "call":
            fn_node = node.child_by_field_name("function")
            if fn_node.type in ("identifier", "attribute"):
                out.append(Call(callee=self._text(fn_node), line=node.start_point[0] + 1))
            for child in node.children:
                self._visit(child, out)
        else:
            for child in node.children:
                self._visit(child, out)

    def _make_loop(self, kind: str, iterated: TSNode, at: TSNode) -> Loop:
        sym, display, root = self._size_symbol(iterated)
        return Loop(
            kind=kind, size_symbol=sym, display=display, root_name=root,
            target_names=[], line=at.start_point[0] + 1,
        )

    # -- size symbols ---------------------------------------------------------

    def _size_symbol(self, node: TSNode) -> tuple[str | None, str, str | None]:
        """Map an iterated expression to (symbol, display, root_name).

        Identical symbols mean "same size"; equating by normalized source text
        is the documented syntactic approximation for v1.
        """
        t = node.type
        text = self._text(node)
        if t == "parenthesized_expression":
            for c in node.named_children:
                return self._size_symbol(c)
        if t == "identifier":
            return f"size:{text}", text, text
        if t == "attribute":
            return f"size:{text}", text, self._root_name(node)
        if t in LITERALS:
            return CONST, text, None
        if t == "call":
            fn = node.child_by_field_name("function")
            arg_node = node.child_by_field_name("arguments")
            args = arg_node.named_children if arg_node else []
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

    # -- helpers --------------------------------------------------------------

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
