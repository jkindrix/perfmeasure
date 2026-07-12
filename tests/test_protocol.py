"""Protocol framing and determinism."""
import io

import pytest

from perfmeasure import protocol


def test_seed_deterministic_and_distinct():
    a = protocol.seed_for("f.py::f", "sorted", 64)
    assert a == protocol.seed_for("f.py::f", "sorted", 64)
    assert a != protocol.seed_for("f.py::f", "random", 64)
    assert a != protocol.seed_for("f.py::f", "sorted", 128)


def test_roundtrip():
    buf = io.StringIO()
    msg = protocol.call_msg("c1", "f.py::f",
                            [{"spec_type": "list_int", "shape": "random",
                              "size": 8, "seed": 1}])
    protocol.write_msg(buf, msg)
    line = buf.getvalue()
    assert line.endswith("\n") and "\n" not in line[:-1]
    assert protocol.parse_msg(line) == msg


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        protocol.parse_msg('"just a string"')


def test_error_kind_checked():
    with pytest.raises(AssertionError):
        protocol.error_result("x", None, "bogus_kind", "msg")
