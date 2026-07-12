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


def test_private_functions_are_not_reported():
    assert "hidden" not in _by_name()   # pub fn in a private mod


def test_fids_are_full_paths():
    assert _by_name()["sum_slice"]["fid"] == "tiny_crate::sum_slice"


def test_byte_slices_are_drivable():
    p = _by_name()["count_zero_bytes"]["params"][0]
    assert p["spec_type"] == "bytes_" and p["style"] == "borrow_slice"


def test_cfg_test_modules_are_not_reported():
    assert "test_helper" not in _by_name()


def test_platform_cfg_is_labeled_not_mislabeled():
    f = _by_name()["windows_only"]
    assert f["drivable"] is False
    assert "cfg_inactive: windows" in f["skip_reason"]


def test_new_whitelist_entries():
    fns = _by_name()
    assert fns["sum_if"]["params"][1]["spec_type"] == "bool_"
    assert fns["join_parts"]["params"][0]["style"] == "borrow_str_slice"
    assert fns["mean"]["params"][0]["spec_type"] == "list_float"
    assert fns["capped_sum"]["params"][1]["spec_type"] == "duration_ms"
