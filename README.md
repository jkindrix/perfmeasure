# perfmeasure

Measures the **empirical time and space complexity** of functions in real
projects: runs each function at doubling input sizes across several input
shapes, measures wall time and peak allocation, and curve-fits both to a
Big-O class. Every answer carries a provenance label — nothing is silently
guessed, nothing is silently omitted.

```sh
perfmeasure fn path/to/file.py::function     # measure one function
perfmeasure fn path/to/file.py               # every drivable function in a file
perfmeasure fn ... --json                    # full records, machine-readable
perfmeasure fn ... --verbose                 # per-shape ladders and stop reasons
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
3. **Measure** — wall time (`perf_counter_ns`, GC paused, warmup, min-of-reps,
   sub-microsecond calls batched) and peak Python-heap allocation
   (`tracemalloc`, separate pass so tracing never distorts timing). Hangs
   are killed and recorded as TIMEOUT points; crashes are data, not failures.
4. **Fit** — `y ~ overhead + b·f(n)` per candidate class
   {1, log n, n, n log n, n², n³, 2ⁿ}, scored by log-space residuals
   (scale-free across decades). Rivals that also explain the data are
   reported as an AMBIGUOUS set instead of being silently dropped.

Provenance labels: `MEASURED` | `AMBIGUOUS(candidates)` |
`UNDRIVABLE(reason)` | `TIMEOUT` | `ERROR`, plus a confidence tier
(`high`/`med`/`low`) driven by fit margin, ladder span, and rep stability.

## Accuracy

The tool is itself evaluated against a ground-truth corpus of
known-complexity functions (`python evals/harness.py`). Current starter
corpus (20 functions, O(1) through O(2ⁿ), plus undrivable-by-design):
**18/18 time classes correct** (14 exact, 4 ambiguous-containing-truth),
**8/8 space classes**, **2/2 undrivable precision**.

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
- **v1 drives typed parameters only**; unhinted params are
  `UNDRIVABLE(missing annotation)` for now (probing is next), as are
  methods, `Callable`s, and custom classes — always with the reason shown.
- Python-only today; the core is language-neutral (abstract input specs
  over a JSON-stdio runner protocol) and a Rust runner is planned.
