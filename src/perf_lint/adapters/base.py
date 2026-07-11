from __future__ import annotations

from typing import Protocol

from perf_lint.ir import Function


class LanguageAdapter(Protocol):
    """Parses source files of one language into the language-neutral IR."""

    extensions: tuple[str, ...]

    def parse(self, path: str, source: bytes) -> list[Function]: ...
