"""Rust discovery on the fixture crate: whitelist, reachability, skips.
Pure tree-sitter — no cargo needed."""
from pathlib import Path

from perfmeasure.languages.rust.discover import discover_crate

CRATE = Path(__file__).parent / "fixtures" / "tiny_crate"


def _by_name():
    return {f["fid"].rpartition("::")[2]: f for f in discover_crate(CRATE)}


def test_drivable_functions_and_types():
    fns = _by_name()
    assert fns["sum_slice"]["drivable"]
    p = fns["sum_slice"]["params"][0]
    assert p["spec_type"] == "list_int" and p["style"] == "borrow_slice"
    assert fns["concat_all"]["params"][0]["spec_type"] == "list_str"
    assert fns["fib"]["params"][0]["spec_type"] == "int_mag"
    assert fns["fib"]["params"][0]["rust_type"] == "u64"


def test_skips_with_reasons():
    fns = _by_name()
    assert fns["takes_generic"]["skip_reason"] == "generic"
    assert "&mut" in fns["takes_mut"]["skip_reason"]
    assert "not_reachable" in fns["hidden"]["skip_reason"]


def test_fids_are_full_paths():
    fns = _by_name()
    assert fns["sum_slice"]["fid"] == "tiny_crate::sum_slice"
    assert fns["hidden"]["fid"] == "tiny_crate::private_mod::hidden"
