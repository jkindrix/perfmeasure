# Changelog

## 0.4.0 (2026-07-12)

Hardening round from an external body-of-work review: everywhere a signal
was silently merged, unbounded, or different from what the record claimed,
it is now split, capped, or labeled.

- **Signal integrity**: timeouts no longer count toward the crash
  blacklist (a hang is steepness data; a crash is a defect — hung probe
  candidates could previously blacklist a function before measurement).
  A call killed only because the per-function deadline shrank its window
  is `deadline_exhausted` — reported as budget exhaustion, never as
  TIMEOUT-because-steep, in ladders and probing alike. The runner's
  internal rep budget is clamped inside the request window, so
  deadline-squeezed calls return partial reps instead of dying mid-rep.
- **Fitting**: the scale estimator is now the geometric mean of
  per-point ratios (the closed-form minimizer of the log-RMSE objective
  the score reports; was median = L1). Robust growth guard (2+2
  endpoint geometric means). New residual-trend check: winner residuals
  climbing with n add the next class up as a rival (the classic hidden
  log factor). `high` confidence now needs 5 doublings of span, 6 for
  an n-vs-n·log·n call. Gate effect on the pre-existing corpus: exact
  rose from 46-48/73 to the mid-50s with a faster wall (final figures
  are machine-written into the README by the gate itself).
- **Rust fidelity**: the harness build mirrors the target workspace's
  own `[profile.release]` (lto, codegen-units, opt-level;
  `panic=unwind` forced and recorded); the effective `opt_profile` with
  named divergences is in every record's environment. Crate naming via
  `cargo metadata` (inline comments and workspace inheritance defeated
  the line parser). Compile-retry runs to a fixed point (max 4 builds).
  `mutates` is reported as unknown (null), not a hardcoded false; a
  first call retaining heap is flagged `state_retained_after_first_call`
  (interior-mutability caches) and demotes confidence; flat
  sub-nanosecond compiled fits are flagged `possible_optimizer_elision`.
- **Mutation correctness**: lean backfill calls (warmup=0) pass
  known-mutation hints and the Python runner detects mutation from the
  first timed call, so backfill reps never measure dirtied inputs.
- **Measurement policy**: memory pass runs GC-disabled (deterministic
  peak envelope, matching the time pass); `fn` mode uses a generous rep
  tier (warmup 2, ≤30 reps, ≥50 ms/size), `scan` stays lean; records
  carry the runner's platform, not the orchestrator's.
- **Eval gate teeth**: per-case candidate-width cap (default 2), mean
  width ceiling, exact-count floor, adjacency-only-pass ratchet,
  unscored-corpus-function failures, undrivable recall split into
  inherent vs tool-limit; five new discriminating corpus cases
  (log n, 2× n log n, n³, 2ⁿ). Projected-cost ladder gate projects the
  full charged wall of the last call, not one rep's time.
- Scan survives a failed runner restart (per-function ERROR; aborts
  only after 3 consecutive), and stray non-protocol stdout lines from a
  runner are skipped with a note instead of read as a hang.

## 0.3.0 (2026-07-12)

- **Deadline semantics**: `--budget` is one monotonic deadline shared by
  probing, ladders, shape scheduling, and request timeouts; `--rescue`
  (default 4s) is the only sanctioned overrun — a named, bounded window
  to kill a hang and salvage a steep ladder. Wall time never exceeds
  budget + rescue.
- Memory is traced on the first five ladder sizes (the fitter's minimum)
  and alternating sizes after, restoring space fits on steep functions.
- `generator_rev` recorded in every JSON record; input generators changed
  in this release (bulk randbytes streams), so records from generator_rev 1
  are not input-reproducible under rev 2.
- Mutable/consuming receiver methods (`&mut self`, `mut self`, `self`)
  measured via fresh instance per rep; Python detects receiver mutation
  by sampled fingerprinting.
- Cache pruning skips entries used in the last hour and touches entries
  on use (approximate LRU).

## 0.2.0 (2026-07-12)

- External-review fixes: always-run-cargo staleness oracle, path-verified
  imports, per-parameter seeds, dict_si/dict_ii split, budget deadline,
  eval scoring rigor (recall, ambiguity width, collision-safe keys),
  richer JSON records, trusted-code documentation.
- Constructor synthesis (`new(args)` with type-inferring empties),
  constructible receivers and instance params, associated-fn discovery,
  async-fn and platform-cfg honesty, probing, mutation and memoization
  handling, wild-corpus drivability tracking.
- ~2.3x faster measurement (bulk generators, 10ms rep floor, alternating
  memory pass, 2^20 collection ceiling).

## 0.1.0 (2026-07-12)

- Initial rebuild as perfmeasure: empirical time+space complexity via
  language-neutral core + per-language runners (Python, Rust harness).
