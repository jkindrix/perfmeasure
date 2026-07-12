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


def test_option_params_get_none():
    p = _by_name()["opt_label"]["params"][1]
    assert p["spec_type"] == "opt_none" and p["style"] == "none"


def test_int_width_variants():
    fns = _by_name()
    u64p = fns["sum_u64"]["params"][0]
    assert u64p["spec_type"] == "list_int" and u64p["cast"] == "u64"
    assert fns["find_byte"]["params"][1]["spec_type"] == "int_mag"


def test_path_params_get_honest_label():
    f = _by_name()["takes_path"]
    assert f["drivable"] is False
    assert "filesystem path" in f["skip_reason"]


def test_async_fns_are_skipped_honestly():
    f = _by_name()["fetch_all"]
    assert f["drivable"] is False
    assert "async" in f["skip_reason"]


def test_impl_associated_fns_are_discovered():
    fns = _by_name()
    assoc = fns["assoc_sum"]
    assert assoc["fid"] == "tiny_crate::Codec::assoc_sum"
    assert assoc["drivable"] is True
    assert "private_helper" not in fns


def test_receiver_methods_on_constructible_types():
    fns = _by_name()
    # unit struct: constructible by name
    wr = fns["with_receiver"]
    assert wr["drivable"] is True and wr["receiver"] == "tiny_crate::Codec"
    # derive(Default)
    sc = fns["scale"]
    assert sc["drivable"] is True
    assert sc["receiver"] == "tiny_crate::Scaler::default()"
    # consuming / &mut receivers stay out, with reasons
    assert "not repeatable" in fns["consume"]["skip_reason"]
    assert "not repeatable" in fns["tweak"]["skip_reason"]
    # no synthesizable constructor (pub field only, no new())
    assert "no synthesizable constructor" in fns["method"]["skip_reason"]


def test_ctor_arg_synthesis():
    fns = _by_name()
    # new(cap: usize, tag: String) -> Self
    idx = fns["lookup_all"]
    assert idx["drivable"] is True
    assert idx["receiver"] == 'tiny_crate::Index::new(1, "x".to_string())'
    # new(&Opts, Option<usize>) -> Result<Self, _>: recursion + unwrap
    ld = fns["count_strict"]
    assert ld["drivable"] is True
    assert ld["receiver"] == \
        "tiny_crate::Loader::new(&tiny_crate::Opts::new(), None).unwrap()"


def test_constructible_struct_params():
    p = _by_name()["validate_with"]["params"][1]
    assert p["spec_type"] == "instance_"
    assert p["style"] == "borrow_ctor"
    assert p["type_ref"] == "tiny_crate::Opts::new()"
