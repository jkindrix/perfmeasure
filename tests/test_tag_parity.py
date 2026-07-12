"""Invariant: every scalable core tag is supported by every runner.

This is the check whose absence let bytes_ exist in the core and the
Python runner while the Rust whitelist silently lacked it. A new tag or a
new runner must keep the matrix complete (or earn a documented exemption
here, visibly)."""
import importlib.util
from pathlib import Path

from perfmeasure.core.model import TAG_SHAPES
from perfmeasure.languages.rust.discover import DECL_TYPES, TYPE_WHITELIST


def _python_runner_spec_types() -> set[str]:
    path = (Path(__file__).parents[1] / "src" / "perfmeasure" / "languages"
            / "python" / "runner_files" / "runner.py")
    spec = importlib.util.spec_from_file_location("_pm_runner", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.SPEC_TYPES)


def test_python_runner_covers_all_core_tags():
    assert set(TAG_SHAPES) <= _python_runner_spec_types()


def test_rust_whitelist_covers_all_core_tags():
    rust_tags = {tag for tag, _style in TYPE_WHITELIST.values()}
    assert set(TAG_SHAPES) <= rust_tags


def test_rust_decl_types_cover_whitelisted_tags():
    rust_tags = {tag for tag, _style in TYPE_WHITELIST.values()}
    assert rust_tags <= set(DECL_TYPES)
