# perf-lint

Static Big-O linter: finds likely-accidental `O(n²)+` code in Python and Rust
projects — nested loops that multiply over real data, linear scans hiding in
loops, `O(n)` helpers called per element.

It is a linter, not a prover: it flags scaling *risks* with an honest
`UNKNOWN` for what it can't analyze (data-dependent `while` bounds, recursion),
and it never guesses.

## Usage

```sh
perf-lint src/                          # scan
perf-lint --diff origin/main .          # only findings new since a revision
perf-lint --adjudicate --llm-model qwen3-coder:30b src/
                                        # LLM-filter noise (local Ollama etc.)
perf-lint --json src/                   # machine-readable, stable finding ids
```

Exit code 1 when findings meet the `--fail-on` threshold (`med` by default).

## How it works

1. **Parse** — tree-sitter grammars feed a language-neutral IR
   (loops + what they iterate, calls, costed operations).
2. **Analyze** — loop-nest products over the same/related collections,
   per-language cost tables (`x in list`, `Vec::contains`, `sort`, string
   concat where the language makes it quadratic), one-level call summaries.
   Severity: `HIGH` = same-collection powers (`O(n²)`), `MED` = independent
   products (`O(n·m)`).
3. **Adjudicate** (optional) — each finding plus its function source and call
   sites goes to any OpenAI-compatible chat endpoint for an
   `ACTIONABLE / BENIGN / WRONG` verdict. Suppress-only, and only on `BENIGN`
   (measured: models hallucinate `WRONG`), fails open on errors.
   Benchmark models against the labeled corpora with
   `python evals/run_eval.py <model>` (Python) and
   `--labels evals/labels-rust.json` (Rust).

   **Measured caveat** (qwen3-coder:30b): on the Python corpus adjudication
   was clean (0/18 real findings lost, 6/7 noise suppressed). On the Rust
   corpus it suppressed the single true finding — a server-controlled
   quadratic in protocol decode — with a confident-but-wrong "bounded by the
   protocol" argument, while filtering only ~half the noise. Treat
   `--adjudicate` as triage, not truth: review the suppressed list
   (`--verbose`) before trusting a clean report, and prefer `exclude` globs
   for tests/benches, which are most of the noise and are filtered
   deterministically.

## Configuration

`.perf-lint.toml` at the target root (CLI flag > env > config):

```toml
exclude = ["*/tests/*", "*/benches/*"]
fail_on = "high"            # high | med | never
llm_model = "qwen3-coder:30b"
llm_url = "http://localhost:11434/v1"
```

Silence a single finding with a comment on or above the line:
`# perf-lint: ignore` (Python) / `// perf-lint: ignore` (Rust).

## Limitations

- Same-collection detection is syntactic; aliasing through function
  boundaries is missed.
- Loops that `break`/`raise` after a bounded number of full passes can be
  overcounted.
- Summaries are one level deep and match bare / `self.` calls only.
- Rust method costs need a resolvable receiver type (param annotation,
  constructor, or usage); unresolved receivers are skipped, not guessed.
