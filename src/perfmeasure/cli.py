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
                          detail=p.get("detail", ""),
                          type_ref=p.get("type_ref"))
                for p in raw.get("params", [])],
        drivable=raw.get("drivable", False),
        skip_reason=raw.get("skip_reason"),
        receiver=raw.get("receiver"),
        receiver_mode=raw.get("receiver_mode"),
        receiver_fill=raw.get("receiver_fill"),
    )


def measure_target(file: str, qualname: str | None, budget: Budget,
                   python: str | None = None, target_root: str | None = None,
                   features: list[str] | None = None):
    """API entry: measure one function (or all drivable in a file).
    Returns (reports, interpreter_note)."""
    if file.endswith(".rs") or (Path(file) / "Cargo.toml").exists():
        return _measure_rust(file, qualname, budget, features=features)
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


def _measure_rust(file: str, qualname: str | None, budget: Budget,
                  features: list[str] | None = None):
    from perfmeasure.languages.rust.plugin import RustPlugin, find_crate_root
    plugin = RustPlugin()
    root = find_crate_root(Path(file))
    functions = plugin.prepare(Path(file), features=features,
                               log=lambda m: print(m, file=sys.stderr))
    if qualname:
        functions = [f for f in functions
                     if f["fid"] == qualname or f["fid"].endswith("::" + qualname)]
        if not functions:
            raise RuntimeError(f"function {qualname!r} not found "
                               f"(fids are crate::module::name)")
    descs = [_descriptor(f) for f in functions]
    if any(f["drivable"] for f in functions):
        session = RunnerSession(plugin.runner_command(root))
    else:
        session = RunnerSession(["true"])   # nothing drivable: never spawned
    try:
        reports = [measure_function(session, d, budget) for d in descs]
    finally:
        session.close()
    return reports, f"cargo harness for {root}"


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
    from perfmeasure.core.model import TOOL_VERSION
    parser.add_argument("--version", action="version",
                        version=f"perfmeasure {TOOL_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    fn = sub.add_parser("fn", help="measure a function (FILE.py::qualname) "
                                   "or all drivable functions in FILE.py")
    fn.add_argument("target")
    fn.add_argument("--budget", type=float, default=30.0,
                    help="deadline in seconds per function (default 30); "
                         "wall time never exceeds budget + --rescue")
    scan_p = sub.add_parser("scan", help="measure every function under DIR")
    scan_p.add_argument("target")
    scan_p.add_argument("--budget", type=float, default=10.0,
                        help="deadline in seconds per function (default 10); "
                             "wall time never exceeds budget + --rescue")
    scan_p.add_argument("--exclude", action="append", default=[],
                        help="glob/substring to skip (repeatable)")
    for p in (fn, scan_p):
        p.add_argument("--json", action="store_true")
        p.add_argument("--verbose", action="store_true")
        p.add_argument("--python", help="target project's interpreter")
        p.add_argument("--features",
                       help="cargo features for the Rust harness "
                            "(comma-separated)")
        p.add_argument("--rescue", type=float, default=4.0,
                       help="bounded overrun window past the deadline to "
                            "kill a hang and salvage the fit (default 4)")
    args = parser.parse_args(argv)

    budget = Budget(per_function_s=args.budget, rescue_s=args.rescue)
    if args.command == "fn":
        # single-function mode: budget affords real statistics per size
        budget = Budget(per_function_s=args.budget, rescue_s=args.rescue,
                        warmup=2, max_repeats=30, min_total_ms=50)
    features = args.features.split(",") if args.features else None
    try:
        if args.command == "fn":
            file, _, qualname = args.target.partition("::")
            reports, interp = measure_target(file, qualname or None, budget,
                                             python=args.python,
                                             features=features)
            summary = None
        else:
            reports, interp, summary = scan_target(
                args.target, budget, python=args.python,
                exclude=args.exclude, features=features)
    except (RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.json:
        if summary is not None:
            import json as _json
            from perfmeasure.core.report import reports_json
            print(_json.dumps({"functions": reports_json(reports),
                               "summary": summary}, indent=2))
        else:
            print(render_json(reports))
    else:
        print(f"# interpreter: {interp}")
        print(render_human(reports, verbose=args.verbose))
        if summary is not None:
            from perfmeasure.core.scan import render_summary
            print(render_summary(summary))
    return 0


def scan_target(target: str, budget: Budget, python: str | None = None,
                exclude: list[str] | None = None,
                features: list[str] | None = None):
    from perfmeasure.core.scan import _summarize, collect_files, scan
    root = Path(target).resolve()
    if (root / "Cargo.toml").exists():
        reports, note = _measure_rust(str(root), None, budget,
                                      features=features)
        return reports, note, _summarize(reports, [])
    plugin = PythonPlugin(python=python)
    interpreter, how = plugin.resolve_interpreter(root)
    files = collect_files([str(root)], plugin.extensions, exclude)
    if not files:
        raise RuntimeError(f"no Python files under {target}")
    session = RunnerSession(plugin.runner_command(root))
    try:
        reports, summary = scan(
            session, files, budget,
            progress=lambda fid: print(f"  measuring {fid.rpartition('/')[2]}",
                                       file=sys.stderr))
    finally:
        session.close()
    return reports, f"{interpreter} (via {how})", summary


if __name__ == "__main__":
    sys.exit(main())
