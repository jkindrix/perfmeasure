"""Deliberately named after a stdlib module: the import path must load
THIS file, not sys.modules['random']."""


def marker_function(xs: list[int]) -> int:
    return len(xs)
