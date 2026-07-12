# perfmeasure

Measures the **empirical time and space complexity** of functions in real
projects: runs each function at doubling input sizes across several input
shapes, measures wall time and peak allocation, and curve-fits both to a
Big-O class. Every answer carries a provenance label — nothing is silently
guessed, nothing is silently omitted.

```sh
perfmeasure fn path/to/file.py::function     # measure one function
perfmeasure fn path/to/file.py               # every drivable function in a file
perfmeasure scan src/                        # whole project + coverage summary
perfmeasure ... --json                       # full records, machine-readable
perfmeasure ... --verbose                    # per-shape ladders and stop reasons
```

```
mod.py::sort_items      T=O(n^2) worst@reversed  S=O(n)  MEASURED [high]
mod.py::merge           T=O(n log n) | O(n) worst@random  S=O(n)  AMBIGUOUS [med]
mod.py::load_config     UNDRIVABLE(unsupported_type: param 'conn' (Connection))
```

## How it works

1. **Discover** — a stdlib-only runner is launched under the *target
   project's own interpreter* (`--python` > `$VIRTUAL_ENV` > `.venv/` >
   fallback), imports the file, and reads real signatures and type hints.
   Nothing is ever installed into the target environment.
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
3. **Measure** — wall time (`perf_counter_ns`, GC paused, warmup, min-of-reps,
   sub-microsecond calls batched) and peak Python-heap allocation
   (`tracemalloc`, separate pass so tracing never distorts timing). Hangs
   are killed and recorded as TIMEOUT points; crashes are data, not failures.
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
known-complexity functions (`python evals/harness.py`): 48 functions,
O(1) through O(2ⁿ) — typed, unhinted (probing), mutating, memoized,
cache-bound, and undrivable-by-design. Current numbers:
**45/45 time classes** (28 exact, rest ambiguous-containing-truth),
**12/12 space classes**, **2/2 undrivable precision**.

## Honest limits

- **Observed, not proven.** Results are measured behavior on generated
  inputs. Shapes catch common worst cases (sorted/reverse/duplicates);
  they do not prove worst-case bounds (e.g. hash-collision attacks).
- **n vs n log n is often AMBIGUOUS** at practical sizes — by design.
  Reporting both beats false precision.
- **Cache effects are real physics.** Random access into multi-MB
  structures scales superlinearly in wall time; a memory-bound O(n) can
  honestly read one class high. The eval corpus encodes this.
- **Space = peak Python-heap allocation.** C-extension allocations
  (numpy buffers, etc.) are invisible to `tracemalloc`.
- **Probed types are educated guesses.** A function tolerating a
  `list[int]` doesn't prove the measurement is semantically meaningful;
  probed results are capped at medium confidence and the generator spec
  is in the JSON record for audit. Methods, `Callable`s, and custom-class
  params stay `UNDRIVABLE` — always with the reason shown.
- Python-only today; the core is language-neutral (abstract input specs
  over a JSON-stdio runner protocol) and a Rust runner is planned.
