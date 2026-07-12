"""Python plugin: resolve the TARGET's interpreter, spawn the stdlib-only
runner under it.

Interpreter resolution order (each step skipped if absent):
  --python flag > $VIRTUAL_ENV > <root>/.venv > <root>/venv > sys.executable
The chosen interpreter is surfaced in every report header; if the target's
imports fail, the fix is `--python`. Nothing is ever installed into the
target environment.
"""
from __future__ import annotations

import os
import sys
from importlib import resources
from pathlib import Path

from perfmeasure.languages.base import LanguagePlugin


def _runner_path() -> str:
    ref = resources.files("perfmeasure.languages.python") / "runner_files" / "runner.py"
    return str(ref)


class PythonPlugin(LanguagePlugin):
    language = "python"
    extensions = (".py",)

    def __init__(self, python: str | None = None):
        self.python_override = python

    def claims(self, path: Path) -> bool:
        return path.suffix in self.extensions or (
            path.is_dir() and any(path.rglob("*.py")))

    def resolve_interpreter(self, target_root: Path) -> tuple[str, str]:
        """(interpreter path, how it was chosen)."""
        if self.python_override:
            return self.python_override, "--python flag"
        venv = os.environ.get("VIRTUAL_ENV")
        if venv and (Path(venv) / "bin" / "python").exists():
            return str(Path(venv) / "bin" / "python"), "$VIRTUAL_ENV"
        for name in (".venv", "venv"):
            cand = target_root / name / "bin" / "python"
            if cand.exists():
                return str(cand), f"{name}/ in target"
        return sys.executable, "tool's own interpreter (fallback)"

    def runner_command(self, target_root: Path) -> list[str]:
        python, _ = self.resolve_interpreter(target_root)
        return [python, "-I", _runner_path(), "--target-root", str(target_root)]
