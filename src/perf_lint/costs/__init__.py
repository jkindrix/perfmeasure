from __future__ import annotations

import tomllib
from pathlib import Path

LINEAR = "linear"
NLOGN = "nlogn"
CONST_COST = "const"
QUADRATIC_GROWTH = "quadratic_growth"


class CostTable:
    def __init__(self, data: dict):
        self._data = data

    def lookup(self, op_kind: str, recv_kind: str) -> str | None:
        if op_kind.startswith("method:"):
            tbl = self._data.get("method", {}).get(op_kind[7:], {})
        elif op_kind.startswith("function:"):
            tbl = self._data.get("function", {}).get(op_kind[9:], {})
        else:
            tbl = self._data.get(op_kind, {})
        return tbl.get(recv_kind) or tbl.get("any")


def load_costs(language: str) -> CostTable:
    path = Path(__file__).parent / f"{language}.toml"
    with open(path, "rb") as f:
        return CostTable(tomllib.load(f))
