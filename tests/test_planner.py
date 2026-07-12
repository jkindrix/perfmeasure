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


def test_shape_fallback_to_random_for_unsupported():
    d = desc([ParamInfo("d", "dict_si"), ParamInfo("keys", "list_str")])
    p, _ = plan(d)
    assert "reversed" in p.shapes          # from list_str
    d_spec, keys_spec = p.specs("reversed", 32)
    assert d_spec.shape == "random"        # dict_si doesn't support reversed
    assert keys_spec.shape == "reversed"
