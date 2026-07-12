"""Drive planning: co-scaling, fixed ints, undrivable reasons."""
from perfmeasure.core.model import FunctionDescriptor, ParamInfo
from perfmeasure.core.planner import plan


def desc(params, drivable=True, skip_reason=None):
    return FunctionDescriptor(fid="f.py::f", file="f.py", line=1,
                              params=params, drivable=drivable,
                              skip_reason=skip_reason)


def test_two_lists_co_scale():
    d = desc([ParamInfo("a", "list_int"), ParamInfo("b", "list_int")])
    p, reason = plan(d)
    assert reason is None
    assert p.driver_params == ["a", "b"]
    specs = p.specs("sorted", 64)
    assert all(s.size == 64 and s.shape == "sorted" for s in specs)


def test_int_fixed_alongside_collection():
    d = desc([ParamInfo("xs", "list_int"), ParamInfo("k", "int_mag")])
    p, _ = plan(d)
    assert p.driver_params == ["xs"]
    assert p.fixed_params == {"k": 1}
    xs_spec, k_spec = p.specs("random", 128)
    assert xs_spec.size == 128
    assert k_spec.size == 1


def test_only_ints_scale_as_magnitude():
    d = desc([ParamInfo("n", "int_mag")])
    p, _ = plan(d)
    assert p.driver_params == ["n"]
    assert p.shapes == ["magnitude"]


def test_omitted_default_not_driven():
    d = desc([ParamInfo("xs", "list_int"),
              ParamInfo("flag", None, omitted=True, detail="has default")])
    p, reason = plan(d)
    assert reason is None
    assert len(p.specs("random", 8)) == 1


def test_untyped_param_is_undrivable_with_reason():
    d = desc([ParamInfo("xs", None, detail="missing annotation")])
    p, reason = plan(d)
    assert p is None
    assert "xs" in reason and "missing annotation" in reason


def test_runner_skip_reason_propagates():
    d = desc([], drivable=False, skip_reason="varargs")
    p, reason = plan(d)
    assert p is None and reason == "varargs"


def test_bool_and_duration_are_fixed_never_scaled():
    d = desc([ParamInfo("xs", "list_int"), ParamInfo("verbose", "bool_"),
              ParamInfo("timeout", "duration_ms")])
    p, reason = plan(d)
    assert reason is None
    assert p.driver_params == ["xs"]
    assert p.fixed_params == {"verbose": False, "timeout": "1ms"}
    xs, verbose, timeout = p.specs("random", 64)
    assert xs.size == 64
    assert verbose.type_tag == "bool_" and verbose.size == 0
    assert timeout.type_tag == "duration_ms" and timeout.size == 1


def test_only_fixed_tags_is_not_scalable():
    d = desc([ParamInfo("verbose", "bool_")])
    p, reason = plan(d)
    assert p is None and reason == "no_scalable_params"


def test_shape_fallback_to_random_for_unsupported():
    d = desc([ParamInfo("d", "dict_si"), ParamInfo("keys", "list_str")])
    p, _ = plan(d)
    assert "reversed" in p.shapes          # from list_str
    d_spec, keys_spec = p.specs("reversed", 32)
    assert d_spec.shape == "random"        # dict_si doesn't support reversed
    assert keys_spec.shape == "reversed"


def test_variants_opt_only_flips_once():
    from perfmeasure.core.planner import variants
    d = desc([ParamInfo("xs", "list_int"),
              ParamInfo("start", "opt_none", type_ref="Some(1usize)")])
    assert variants(d) == [(0, False), (0, True)]
    p, _ = plan(d, fixed_variant=0, opt_some=True)
    specs = p.specs("random", 8)
    assert specs[1].type_tag == "opt_some"
    assert p.fixed_params["start"] == "Some(1usize)"


def test_variants_no_fallbacks_single():
    from perfmeasure.core.planner import variants
    d = desc([ParamInfo("xs", "list_int")])
    assert variants(d) == [(0, False)]
    p, _ = plan(d)
    assert not p.has_variants


def test_variants_ints_and_opt_compose():
    from perfmeasure.core.planner import variants
    d = desc([ParamInfo("xs", "list_int"), ParamInfo("k", "int_mag"),
              ParamInfo("o", "opt_none", type_ref="Some(1u8)")])
    assert len(variants(d)) == 6


def test_opt_without_some_expr_stays_none():
    from perfmeasure.core.planner import variants
    d = desc([ParamInfo("xs", "list_int"), ParamInfo("o", "opt_none")])
    assert variants(d) == [(0, False)]
    p, _ = plan(d, opt_some=True)     # flip without an expr: stays None
    assert p.specs("random", 8)[1].type_tag == "opt_none"


def test_receiver_fill_scales_and_holds_ints_fixed():
    d = FunctionDescriptor(fid="f.py::C.add", file="f.py", line=1,
                           params=[ParamInfo("value", "int_mag")],
                           drivable=True, receiver="m:C",
                           receiver_fill="ctor")
    p, reason = plan(d)
    assert reason is None
    assert p.receiver_scaled and p.driver_params == ["self(ctor)"]
    specs = p.specs("random", 64)
    assert specs[-1].type_tag == "recv_fill" and specs[-1].size == 64
    assert specs[0].size == 1          # int arg held fixed


def test_receiver_without_fill_stays_fixed_point():
    d = FunctionDescriptor(fid="f.py::C.scale", file="f.py", line=1,
                           params=[ParamInfo("xs", "list_int")],
                           drivable=True, receiver="m:C")
    p, _ = plan(d)
    assert not p.receiver_scaled
    assert all(s.type_tag != "recv_fill" for s in p.specs("random", 8))
