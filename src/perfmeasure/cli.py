"""perfmeasure CLI.

  perfmeasure fn FILE.py::qualname | CRATE::mod::fn   measure one function
  perfmeasure fn FILE.py                        every drivable fn in a file
  perfmeasure scan DIR|CRATE                    whole tree + coverage summary
  perfmeasure diff DIR --baseline B.json        exit 1 on class regression

Options: --json, --budget SECONDS, --rescue SECONDS, --verbose,
--python PATH (Python targets), --features LIST (Rust targets),
--exclude GLOB (scan/diff).
"""
from __future__ import annotations

import argparse
import os
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
        if python:
            print("warning: --python applies to Python targets only; "
                  "ignored for a Rust target", file=sys.stderr)
        return _measure_rust(file, qualname, budget, features=features)
    if features:
        print("warning: --features applies to Rust targets only; "
              "ignored for a Python target", file=sys.stderr)
    file_path = Path(file).resolve()
    root = Path(target_root).resolve() if target_root else _guess_root(file_path)
    plugin = PythonPlugin(python=python)
    interpreter, how = plugin.resolve_interpreter(root)
    session = RunnerSession(plugin.runner_command(root))
    try:
        # fids are target-root-relative (portable identity); match that
        only = (f"{os.path.relpath(file_path, root)}::{qualname}"
                if qualname else None)
        resp = session.request(
            protocol.discover_msg(session.next_id(), [str(file_path)], only),
            timeout=60.0)
        if resp["op"] == "error":
            tail = "\n".join((resp.get("detail") or {}).get("stderr_tail", []))
            raise RuntimeError(
                f"discovery failed: {resp['message']}"
                + (f"\nrunner stderr:\n{tail}" if tail else ""))
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
    diff_p = sub.add_parser(
        "diff", help="re-measure DIR and fail (exit 1) on any function "
                     "whose complexity class regressed vs a --baseline "
                     "JSON (from `scan --json`)")
    diff_p.add_argument("target")
    diff_p.add_argument("--baseline", required=True,
                        help="baseline JSON from a previous `scan --json`")
    diff_p.add_argument("--budget", type=float, default=10.0)
    diff_p.add_argument("--exclude", action="append", default=[])
    diff_p.add_argument("--strict", action="store_true",
                        help="also fail on continuity losses: baseline "
                             "functions that vanished or no longer measure")
    for p in (fn, scan_p, diff_p):
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
    if args.command == "diff":
        import json as _json
        from perfmeasure.core.diff import diff_reports, render_diff
        raw = _json.loads(Path(args.baseline).read_text())
        baseline = raw["functions"] if isinstance(raw, dict) else raw
        result = diff_reports(reports, baseline)
        if args.json:
            print(_json.dumps(result, indent=2))
        else:
            print(render_diff(result))
        if baseline and not result["matched"]:
            # zero comparisons must never read as green (a baseline from
            # another checkout used to produce exactly this and exit 0)
            print(f"error: 0 of {len(baseline)} baseline functions "
                  "matched the current measurement — nothing was "
                  "compared. Baselines from before 0.7.0 use "
                  "absolute-path ids; regenerate with `scan --json`.",
                  file=sys.stderr)
            return 1
        if result["regressions"]:
            return 1
        if args.strict and result["continuity"]:
            return 1
        return 0
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
        if python:
            print("warning: --python applies to Python targets only; "
                  "ignored for a Rust target", file=sys.stderr)
        reports, note = _measure_rust(str(root), None, budget,
                                      features=features)
        return reports, note, _summarize(reports, [])
    if features:
        print("warning: --features applies to Rust targets only; "
              "ignored for a Python target", file=sys.stderr)
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
