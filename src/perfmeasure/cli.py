"""perfmeasure CLI.

  perfmeasure fn path/to/file.py::qualname     measure one function
  perfmeasure fn path/to/file.py               measure every drivable
                                               function in the file
Options: --json, --budget SECONDS, --python PATH, --verbose
(`perfmeasure scan DIR` arrives in M2.)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from perfmeasure import protocol
from perfmeasure.core.ladder import Budget
from perfmeasure.core.model import FunctionDescriptor, ParamInfo
from perfmeasure.core.orchestrator import measure_function
from perfmeasure.core.report import render_human, render_json
from perfmeasure.languages.python.plugin import PythonPlugin
from perfmeasure.session import RunnerSession


def _descriptor(raw: dict) -> FunctionDescriptor:
    return FunctionDescriptor(
        fid=raw["fid"], file=raw.get("file", ""), line=raw.get("line", 0),
        params=[ParamInfo(name=p["name"], spec_type=p.get("spec_type"),
                          omitted=p.get("omitted", False),
                          detail=p.get("detail", ""))
                for p in raw.get("params", [])],
        drivable=raw.get("drivable", False),
        skip_reason=raw.get("skip_reason"),
    )


def measure_target(file: str, qualname: str | None, budget: Budget,
                   python: str | None = None, target_root: str | None = None):
    """API entry: measure one function (or all drivable in a file).
    Returns (reports, interpreter_note)."""
    file_path = Path(file).resolve()
    root = Path(target_root).resolve() if target_root else _guess_root(file_path)
    plugin = PythonPlugin(python=python)
    interpreter, how = plugin.resolve_interpreter(root)
    session = RunnerSession(plugin.runner_command(root))
    try:
        only = f"{file_path}::{qualname}" if qualname else None
        resp = session.request(
            protocol.discover_msg(session.next_id(), [str(file_path)], only),
            timeout=60.0)
        if resp["op"] == "error":
            raise RuntimeError(f"discovery failed: {resp['message']}")
        descs = [_descriptor(f) for f in resp["functions"]]
        if qualname and not descs:
            raise RuntimeError(
                f"function {qualname!r} not found in {file} "
                "(note: names starting with '_' are skipped)")
        reports = [measure_function(session, d, budget) for d in descs]
    finally:
        session.close()
    return reports, f"{interpreter} (via {how})"


def _guess_root(file_path: Path) -> Path:
    d = file_path.parent
    while (d / "__init__.py").exists():
        d = d.parent
    for up in (d, *d.parents):
        if (up / "pyproject.toml").exists() or (up / "setup.py").exists() \
                or (up / ".git").exists():
            return up
    return d


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="perfmeasure",
        description="Measure empirical time and space complexity.")
    sub = parser.add_subparsers(dest="command", required=True)
    fn = sub.add_parser("fn", help="measure a function (FILE.py::qualname) "
                                   "or all drivable functions in FILE.py")
    fn.add_argument("target")
    fn.add_argument("--json", action="store_true")
    fn.add_argument("--verbose", action="store_true")
    fn.add_argument("--budget", type=float, default=30.0,
                    help="wall-clock seconds per function (default 30)")
    fn.add_argument("--python", help="target project's interpreter")
    args = parser.parse_args(argv)

    file, _, qualname = args.target.partition("::")
    budget = Budget(per_function_s=args.budget)
    try:
        reports, interp = measure_target(file, qualname or None, budget,
                                         python=args.python)
    except (RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(render_json(reports))
    else:
        print(f"# interpreter: {interp}")
        print(render_human(reports, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
