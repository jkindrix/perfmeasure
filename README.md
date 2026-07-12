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
uv tool install git+https://github.com/jkindrix/perfmeasure
# or from a clone: uv tool install . / pipx install . / pip install .
```

Requires Python ≥ 3.10 on a Unix platform; Rust measurement additionally
needs a `cargo` toolchain. Nothing is ever installed into target
environments. Target projects may run their own interpreter (`--python`,
`$VIRTUAL_ENV`, `.venv/`) down to **Python 3.9** — the runner needs
`tracemalloc.reset_peak` and `random.randbytes` (both 3.9), verified by
driving a 3.9 target end-to-end; on older targets measurement fails
honestly (the AttributeError lands in the per-shape failure records)
rather than silently.

```sh
perfmeasure fn path/to/file.py::function     # measure one Python function
perfmeasure fn path/to/crate::module::func   # measure one Rust pub fn
perfmeasure fn path/to/file.py               # every drivable function in a file
perfmeasure scan src/                        # whole project + coverage summary
perfmeasure scan path/to/crate               # Cargo crate (library target)
perfmeasure scan src/ --json > baseline.json # save a complexity baseline
perfmeasure diff src/ --baseline baseline.json  # exit 1 if any function's
                                             # class regressed (one-sided,
                                             # ambiguity-robust; overlap
                                             # warns instead of failing)
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
   in Python (separate pass, GC paused there too, so tracing never
   distorts timing and cycle collection never shrinks the peak
   nondeterministically); a counting `#[global_allocator]` in the
   generated Rust harness (sees every heap byte, built `--release` under
   the **target workspace's own `[profile.release]`** — lto,
   codegen-units, opt-level mirrored; `panic=unwind` forced and any
   divergence recorded in the JSON `opt_profile` — with `black_box` so
   the optimizer can't delete the work, panics caught as structured
   errors). Single-function mode (`fn`) uses a generous rep tier
   (warmup 2, up to 30 reps, ≥50 ms per size); `scan` stays lean. Hangs
   are killed and recorded as TIMEOUT points; crashes are data, not
   failures; a call killed only because the per-function budget ran out
   is labeled `deadline_exhausted` — a scheduling fact, never blamed on
   the function.
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
Current run: **93/93 time classes** (82 exact, rest ambiguous-containing-truth, mean ambiguity width 1.62), **29/29 space classes**, **10/10 undrivable recall** — full gate in ~180 s.
<!-- gate:end -->
One honest caveat: the fitter's thresholds are calibrated against this
same corpus, so the gate measures fit-to-corpus plus regression teeth,
not held-out generalization — there is no independent accuracy proof.
Drivability on real projects is tracked separately as a regression
metric (`python evals/wild.py`); wild functions have no ground-truth
classes, so that sweep checks drivability, not accuracy.

## Honest limits

- **Observed, not proven.** Results are measured behavior on generated
  inputs. Shapes catch common worst cases (sorted/reverse/duplicates);
  they do not prove worst-case bounds (e.g. hash-collision attacks).
- **n vs n log n is often AMBIGUOUS in wall time** at practical sizes —
  by design; reporting both beats false precision. The **instructions
  channel** (Linux, Python targets: retired-instruction counts via
  perf_event, <2% worst-point run-to-run variance measured) resolves
  most of these: a clean instruction
  fit one class below the wall headline becomes the headline
  (`wall_cache_inflated` names the wall reading, which stays in the
  candidate set). No perf access (containers with
  `perf_event_paranoid > 1`, non-Linux) just means wall-time-only
  answers, exactly as before. Rust instruction counting is not yet
  wired (planned: callgrind).
- **Cache effects are real physics.** Random access into multi-MB
  structures scales superlinearly in wall time; a memory-bound O(n) can
  honestly read one class high — the wall channel keeps saying so even
  when the instructions channel corrects the class. The eval corpus
  encodes this.
- **Space semantics are per-language** (declared in every record):
  Python's `tracemalloc` is blind to C-extension allocations (numpy
  buffers, etc. — flagged when the return value's size betrays it);
  Rust's counting allocator sees everything heap. Never compare absolute
  bytes across languages.
- **Compiled code bends harder.** Rust has no interpreter cushion, so
  cache-hierarchy transitions in memory-bound linear code read up to one
  class high; a tail-of-ladder cross-check reports the stabilized
  asymptote as a candidate. Allocator threshold events (a sort's scratch
  buffer, a `Vec` growth policy) step the constant mid-ladder instead of
  bending it — detected as two flat per-element runs split by one jump
  (`coefficient_step_suspected`), which keeps the true class in the
  candidates and demotes confidence.
- **Budget truncation is named.** A time-O(1) verdict whose every ladder
  was stopped by the budget — never by the size ceiling or the data —
  is flagged `constant_within_budget_window` with the window's max n on
  the headline line: a large constant can mask a term that had no room
  to emerge. Likewise a hard timeout just past the fitted window that
  the fitted class cannot explain (20x+ single-call overrun against its
  own extrapolation) is flagged `timeout_above_window` — evidence of a
  steeper regime beyond the window, not proof.
- **Probed types are educated guesses.** A function tolerating a
  `list[int]` doesn't prove the measurement is semantically meaningful;
  probed results are capped at medium confidence and the generator spec
  is in the JSON record for audit.
- **Batched points are warm-cache points.** Sub-10 µs calls are batched
  over the same input buffers (like every microbenchmark harness), so
  memory-bound cost can read low at small n; points carry
  `batched: true` so the regime is auditable. `black_box` is best-effort
  by its documented contract — a nontrivial compiled function reading
  flat at sub-nanosecond per call is flagged
  `possible_optimizer_elision` rather than reported as a measurement.
- **First-call state retention is flagged.** A Rust `&self` method whose
  first call leaves heap behind (memo table, interner, lazy static) gets
  `state_retained_after_first_call` and a confidence demotion — later
  reps may have measured the cached path. Python memoizers are refit on
  first-call timings (`suspected_memoization`).
- **Receiver-scaled methods are measured on the cold path.** When a
  receiver type has a len-verified fill strategy (iterable constructor,
  `update`/`extend`/`add`/`append`/`push`), methods are driven against
  receivers of n items (`receiver_scaled`) — `SortedList.add` reads its
  class in receiver size instead of a vacuous empty-instance O(1). But a
  method that MUTATES the receiver — including one that only builds a
  lazy internal cache (sortedcontainers' positional `_index`) — gets a
  fresh instance per rep, so it is measured as a first call every time:
  the warm amortized class may be lower than the reported cold one.
  Containers with large fixed load factors (sqrt-list, wide B-trees)
  honestly read a class low below `load²` elements — measured truth of
  the tested regime, not the asymptote.
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

## Prior art

Empirical complexity inference is not new: [pberkes/big_O](https://github.com/pberkes/big_O)
(Python, time only), [plasma-umass/bigO](https://github.com/plasma-umass/bigO)
(Python, time+space, passively observes whatever inputs your program
happens to run), Meta's [BigO(Bench)](https://facebookresearch.github.io/BigOBench/)
(Python: its dynamic framework actively scales inputs and fits both
time and space classes — built to benchmark LLMs on
competitive-programming tasks, not to point at your codebase),
[zertyz/big-O](https://github.com/zertyz/big-O) (Rust, time+space via a
counting allocator, asserted inside hand-written `big-o-test` tests),
and Google Benchmark's `.Complexity()` (C++, time only) all fit classes
to measurements. What none of them offer, and perfmeasure does, is the
combination: **discovery** (point it at a file, crate, or tree — no
per-function test or benchmark to write), shape-controlled doubling
ladders with worst-across-tested-shapes reporting, time **and** space,
explicit AMBIGUOUS sets with provenance and confidence on every answer,
and both Python and Rust from one tool — the generated
instrumented-`--release` Rust harness that mirrors the target
workspace's own release profile has no peer we know of.
