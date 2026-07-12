"""Rust plugin: tree-sitter discovery core-side, generated harness binary
as the runner. Unlike Python, discovery happens before any process exists
(the harness must be generated from it), so the plugin exposes
`prepare()` returning descriptors + the binary to spawn."""
from __future__ import annotations

from pathlib import Path

from perfmeasure.languages.base import LanguagePlugin
from perfmeasure.languages.rust.discover import crate_name, discover_crate
from perfmeasure.languages.rust.harness_gen import build_harness


def find_crate_root(path: Path) -> Path:
    p = path.resolve()
    for d in ([p] if p.is_dir() else []) + list(p.parents):
        if (d / "Cargo.toml").exists():
            return d
    raise RuntimeError(f"no Cargo.toml above {path}")


class RustPlugin(LanguagePlugin):
    language = "rust"
    extensions = (".rs",)

    def __init__(self):
        self._binary: Path | None = None

    def claims(self, path: Path) -> bool:
        return path.suffix in self.extensions or (
            path.is_dir() and (path / "Cargo.toml").exists())

    def prepare(self, target: Path, features: list[str] | None = None,
                log=print) -> list[dict]:
        """Discover + build. Returns raw function dicts (including
        undrivable ones, with reasons) ready for descriptor conversion."""
        root = find_crate_root(target)
        crate = crate_name(root / "Cargo.toml")
        functions = discover_crate(root)
        if any(f["drivable"] for f in functions):
            self._binary = build_harness(root, crate, functions,
                                         features=features, log=log)
        return functions

    def runner_command(self, target_root: Path) -> list[str]:
        if self._binary is None:
            raise RuntimeError("prepare() must run before runner_command()")
        return [str(self._binary)]
