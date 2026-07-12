# perfmeasure

Measures the **empirical time and space complexity** of functions in real
projects — Python and Rust — by running each function at doubling input
sizes across several input shapes, measuring wall time and peak
allocation, and curve-fitting both to a Big-O class. Every answer carries
a provenance label — nothing is silently guessed, nothing is silently
omitted.

> **Trusted code only.** perfmeasure imports, constructs, and executes
> the target's code with your full user privileges — file system,
> network, subprocesses. The runner subprocess boundary provides protocol
> stability and hard-kill containment, **not** an OS sandbox. Do not
> point it at code you would not run yourself. Rust measurement
> additionally requires a Unix platform (the harness uses fd-level
> stream redirection).

## Installation

```sh
uv tool install /path/to/perf-lint      # or: pipx install / pip install .
```

Requires Python ≥ 3.10 on a Unix platform; Rust measurement additionally
needs a `cargo` toolchain. Nothing is ever installed into target
environments.

```sh
perfmeasure fn path/to/file.py::function     # measure one Python function
perfmeasure fn path/to/crate::module::func   # measure one Rust pub fn
perfmeasure fn path/to/file.py               # every drivable function in a file
perfmeasure scan src/                        # whole project + coverage summary
perfmeasure scan path/to/crate               # Cargo crate (library target)
perfmeasure ... --json                       # full records, machine-readable
perfmeasure ... --verbose                    # per-shape ladders and stop reasons
```

```
mod.py::sort_items      T=O(n^2) worst@reversed  S=O(n)  MEASURED [high]
mod.py::merge           T=O(n log n) | O(n) worst@random  S=O(n)  AMBIGUOUS [med]
mod.py::load_config     UNDRIVABLE(unsupported_type: param 'conn' (Connection))
```

## How it works

1. **Discover** — Python: a stdlib-only runner is launched under the
   *target project's own interpreter* (`--python` > `$VIRTUAL_ENV` >
   `.venv/` > fallback), imports the file, and reads real signatures and
   type hints; nothing is ever installed into the target environment.
   Rust: a tree-sitter scan finds crate-root-reachable `pub fn`s whose
   parameter types are on a textual whitelist (`&[i64]`, `Vec<i64>`,
   `&str`, `String`, integer types, `HashMap<i64,i64>`, …) — types are
   never resolved, and functions the compiler later rejects are dropped
   with `harness_compile_failed` by a parse-errors-and-rebuild retry.
2. **Drive** — parameters with supported hints (`list[int]`, `str`, `int`,
   `dict[str,int]`, `set[int]`, …) are generated at doubling sizes across
   input **shapes** — random, sorted, reversed, duplicate-heavy, all-equal —
   because random-only measurement hides worst cases (quicksort looks
   O(n log n) on random data). All scalable params grow together; the
   headline class is the **worst across shapes**, with the shape named.
   Unhinted parameters are **probed** (name heuristics order candidate
   types; a candidate must survive two live calls) — probed results are
   labeled `type_source: probed` and capped at medium confidence. Held-fixed
   int params walk a fallback ladder (1, 0, half-of-driver) before the
   function is declared undrivable.
3. **Measure** — wall time (GC paused in Python, warmup, min-of-reps,
   sub-microsecond calls batched) and peak heap allocation — `tracemalloc`
   in Python (separate pass so tracing never distorts timing); a counting
   `#[global_allocator]` in the generated Rust harness (sees every heap
   byte, built `--release` with `black_box` so the optimizer can't delete
   the work, panics caught as structured errors). Hangs are killed and
   recorded as TIMEOUT points; crashes are data, not failures.
   In-place mutators are detected by input fingerprinting and re-driven on
   fresh inputs each rep (`mutates_input`); memoized functions are detected
   by warmup-vs-rep timing and refit on first-call times
   (`suspected_memoization`); a return value that grows while traced peak
   stays flat demotes the space answer (`untracked_alloc_suspected`).
4. **Fit** — `y ~ overhead + b·f(n)` per candidate class
   {1, log n, n, n log n, n², n³, 2ⁿ}, scored by log-space residuals
   (scale-free across decades). Rivals that also explain the data are
   reported as an AMBIGUOUS set instead of being silently dropped.

Provenance labels: `MEASURED` | `AMBIGUOUS(candidates)` |
`UNDRIVABLE(reason)` | `TIMEOUT` | `ERROR`, plus a confidence tier
(`high`/`med`/`low`) driven by fit margin, ladder span, and rep stability.

## Accuracy

The tool is itself evaluated against a ground-truth corpus of
known-complexity functions (`python evals/harness.py`) spanning Python
and Rust, O(1) through O(2ⁿ) — typed, unhinted (probing), mutating,
memoized, cache-bound, panicking, methods, constructed instances, and
undrivable-by-design.
<!-- gate:begin (written by `python evals/harness.py --update-readme`; do not edit) -->
Current run: **73/73 time classes** (48 exact, rest ambiguous-containing-truth, mean ambiguity width 1.34), **20/20 space classes**, **7/7 undrivable recall** — full gate in ~177 s.
<!-- gate:end -->
Drivability on real projects is tracked separately as a regression
metric (`python evals/wild.py`).

## Honest limits

- **Observed, not proven.** Results are measured behavior on generated
  inputs. Shapes catch common worst cases (sorted/reverse/duplicates);
  they do not prove worst-case bounds (e.g. hash-collision attacks).
- **n vs n log n is often AMBIGUOUS** at practical sizes — by design.
  Reporting both beats false precision.
- **Cache effects are real physics.** Random access into multi-MB
  structures scales superlinearly in wall time; a memory-bound O(n) can
  honestly read one class high. The eval corpus encodes this.
- **Space semantics are per-language** (declared in every record):
  Python's `tracemalloc` is blind to C-extension allocations (numpy
  buffers, etc. — flagged when the return value's size betrays it);
  Rust's counting allocator sees everything heap. Never compare absolute
  bytes across languages.
- **Compiled code bends harder.** Rust has no interpreter cushion, so
  cache-hierarchy transitions in memory-bound linear code read up to one
  class high; a tail-of-ladder cross-check reports the stabilized
  asymptote as a candidate.
- **Probed types are educated guesses.** A function tolerating a
  `list[int]` doesn't prove the measurement is semantically meaningful;
  probed results are capped at medium confidence and the generator spec
  is in the JSON record for audit.
- **Methods and instance params are measured against constructed
  state.** Methods and struct/class-typed params are driven with a fixed
  fresh instance built via `Default`, `new()`, unit structs, Python
  `Cls()` — or, when a constructor takes arguments, **synthesized args**
  (small scalars, type-inferring empty containers, `None`, recursion for
  nested types). The constructor expression is named in the record,
  results are flagged `fixed_instance_inputs` and capped at medium
  confidence, because cost that depends on instance state is invisible
  at that fixed point. `&mut self` and consuming receivers get a **fresh
  instance per rep** (constructed untimed) so mutation never leaks
  between reps — flagged `mutates_receiver`; Python detects receiver
  mutation at runtime by fingerprinting and does the same. Types whose
  constructors need unsynthesizable values stay `UNDRIVABLE`, naming
  the type.
- **Rust reaches public, non-generic functions and methods of library
  crates** (`&mut` *parameters* remain skipped) — every skip carries its
  reason, and the scan summary counts them. Type aliases stay undrivable
  by design (resolving them is the semantic-model trap this architecture
  refuses).
- Adding a language = writing a runner that speaks the JSON-stdio
  protocol (abstract input specs in, seconds + peak bytes out) — the
  core never learns language semantics.
