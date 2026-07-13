# Changelog

## 0.7.0 (2026-07-13)

Burn-down from an independent external audit (different reviewer, every
finding re-verified and reproduced here before acting). BREAKING:
Python fids and therefore baselines and input seeds change — regenerate
baselines with `scan --json` (an old baseline now fails the diff gate
loudly instead of comparing nothing and passing).

- **Portable function ids** (the audit's top finding, reproduced: a
  baseline diffed from a moved checkout compared ZERO functions and
  exited 0). Python fids are now target-root-relative instead of
  absolute (`tests/fixtures/mod.py::fn`); the absolute path stays in
  `file` for diagnostics. Input seeds derive from fids, so generated
  inputs are now also reproducible across checkouts — GENERATOR_REV 4.
- **Diff gate integrity**: a baseline that matches nothing fails with a
  regenerate hint (zero comparisons must never read as green — same
  policy as the wild gate); continuity losses (vanished /
  no-longer-measured functions) are their own category, warning by
  default and failing under `--strict` (borderline ladders flip with
  machine timing; a default-fail would flap); a baseline measured under
  a different `generator_rev` is prominently called out.
- **Mutation fingerprints see content, not just shape** (reproduced: a
  same-length set churner drew no `mutates_input` and re-timed dirtied
  state). Set fingerprints sample content; dict fingerprints sample a
  spread instead of the first items (the same hole, unflagged by the
  audit, one key past the sampled head). Pinned by corpus fixtures
  (`set_churn`, `dict_value_churn`) and unit tests.
- **Rust harness honesty**: `warmup: 0` now truly runs no warmup
  (probing and lean rescue calls were silently warmed and budget-taxed;
  `warmup_seconds` reports null like Python); owned `Option<T>` values
  ride the per-call prep tuple instead of being moved by the first call
  (an `Option<String>` param used to kill the whole harness compile —
  pinned by `rs_opt_string_tag`); undrivable records no longer claim
  `tracemalloc` on Rust targets (`allocator` defaults to "unknown"
  until a runner declares itself).
- **Release-profile mirroring on Python 3.10**: `tomli` backfills
  `tomllib`, so the README's mirroring claim holds on every supported
  interpreter.
- **Prior art**: adds bigocheck (Python, time+space, case analysis,
  JSON regression baselines, single-named-function scope; verified on
  PyPI).

## 0.6.1 (2026-07-12)

- **Python 3.9 targets actually work now.** On 3.9/3.10,
  `isinstance(list[int], type)` is True, so the runner's unguarded
  `issubclass(hint, os.PathLike)` check raised TypeError and killed
  discovery for any 3.9 target using PEP 585 builtin generics — the 0.6.0
  floor verification had used `typing.List` hints and missed it. Found by
  the new CI floor check on its first run. Guarded; pinned by a
  conformance test that discovers builtin-generic hints under a real 3.9
  (skipped where none is available).
- **Runner deaths are no longer opaque**: `discovery failed: runner died
  (exit 1)` now appends the runner's stderr tail, which is exactly what
  diagnosing the above required.

## 0.6.0 (2026-07-12)

Burn-down from a two-track external review: a body-of-work review
(claims-vs-reality, publication hygiene) and a metrology audit that ran
falsification experiments against ground-truth fixtures. The audit
FALSIFIED one documented guarantee and surfaced two more
confident-but-wrong output modes; all three shared a root cause — a
single regime step inside the fitted window — and the fix targets the
class, not the instances.

- **Coefficient-step check** (the falsified guarantee): an allocator or
  threshold event that steps the constant of the true class mid-ladder
  (sort scratch buffers: 8 -> 16 B/elem) used to yield a
  high-confidence wrong singleton one class up, because no fixed re-fit
  window can dodge a step that may straddle any of them. The fitter now
  detects the step signature itself — two flat per-point scale runs
  split by one jump, which a genuinely higher class cannot produce —
  keeps the true class in the candidates, names the step in the fit
  reason, sets `coefficient_step_suspected`, and demotes confidence.
  Pinned by a new corpus fixture (`step_alloc`) with an `expect_flag`
  assertion, and by unit tests including the smooth-log-drift control.
- **Space adjacency ratchet**: the gate's adjacency-only ratchet counted
  time passes only, so space-side class inflation was structurally
  unratcheted (the falsified case hid exactly there). Space passes that
  exist only via `space_any` adjacency are now counted and capped
  (`SPACE_ADJACENT_ONLY_MAX = 2`).
- **Budget-truncated O(1) is named**: a time-O(1) verdict whose every
  ladder was stopped by the budget — never by n_max or the data — now
  carries `constant_within_budget_window` (with the window's max n) on
  the headline line and a confidence demotion: a large constant can
  mask a term that had no room to emerge. A true cheap O(1) reaches
  n_max and never flags.
- **Fit-defying timeouts surface**: a hard timeout above the fitted
  window that the fitted class cannot explain (20x+ single-call overrun
  against its own extrapolation, leaving headroom for warmup + reps +
  the traced pass) is flagged `timeout_above_window` on the headline
  line. A steep class that honestly outgrew the timeout predicts the
  kill and stays quiet.
- **Worst-shape ties break on measured cost**: same-class shapes were
  tie-broken by iteration order (insertion sort read `worst@random`
  while reversed measured 2x slower); ties now break on cost at the
  largest size every tied shape reached, for time and space worst
  shapes both. Gate gains positive `worst_shape` assertions.
- **Confidence coherence**: non-measurements (UNDRIVABLE/TIMEOUT/ERROR)
  report `confidence: null` instead of a vacuous default;
  `untracked_alloc_suspected` now demotes confidence like every other
  suspicion flag.
- **Target-interpreter floor stated and tested**: Python targets need
  >= 3.9 (`tracemalloc.reset_peak`, `random.randbytes`) — verified by
  driving 3.8 (fails honestly, AttributeError in the failure records)
  and 3.9 (measures end-to-end) via `--python`; CI pins the 3.9 floor
  every run.
- **Publication hygiene**: CI workflow (pytest + wheel build blocking;
  the accuracy gate observational until it earns a CI track record);
  full wheel metadata (readme, urls, authors, classifiers); prior art
  now names zertyz/big-O and characterizes BigO(Bench)'s active
  time+space framework fairly; the README states the eval-calibration
  circularity (thresholds tuned on the same corpus the gate scores);
  `--python`/`--features` warn instead of being silently ignored on the
  wrong language; the wild gate prints its coverage and fails when zero
  targets exist instead of passing vacuously; the wild gate also now
  regresses on the STRUCTURAL drivability count (measured +
  budget-bound TIMEOUT/insufficient_range) — borderline ladders flip
  between measured and insufficient_range with machine timing (observed:
  53-57 measured, structural invariant at 57), so gating on measured
  alone cried wolf; stale CLI docstring
  rewritten; version sync (pyproject vs `TOOL_VERSION`) is now a test;
  the instructions-channel variance claim was re-measured and corrected
  (<2% worst-point run-to-run, previously stated <1%).

## 0.5.0 (2026-07-12)

Burn-down round from field-testing against fresh real-world targets
(humanize, memchr, bytecount, sortedcontainers), competitor archaeology
(plasma-umass/bigO, zertyz/big-O, pberkes/big_O, iai-callgrind), and
literature research. Every gap class found in the field got a fix plus
eval-corpus teeth.

- **Instructions channel** (Linux, Python targets): retired-instruction
  counts via perf_event (<1% variance) as a scale-free second channel.
  A clean instruction fit one class below the wall headline becomes the
  headline (`wall_cache_inflated` keeps the wall reading in candidates).
  A per-element trend test sharpens the {n, n log n} pair further. Gate
  exact: 66 -> ~80/92; adjacency-only passes 5 -> 0-2; ratchets
  tightened (EXACT_FLOOR 52 -> 72, ADJACENT_ONLY_MAX 8 -> 4). Rust
  instruction counting (callgrind) is planned, not yet wired.
- **Receiver scaling**: methods on len-verifiable fillable receivers
  (iterable ctor, update/extend/add/append/push) are measured against
  receivers of n items — `SortedList.add` reads its class in receiver
  size instead of a vacuous empty-instance O(1). Probing also runs
  against filled receivers. Mutating (including lazy-cache-building)
  methods get a fresh filled instance per rep: cold-path semantics,
  documented.
- **Drivability gap classes** (field-found): multi-member union hints
  drive their first generatable member; plain float params (float_mag,
  Python + Rust f64/f32); list[Any]; per-param eval fallback when
  get_type_hints fails wholesale (TYPE_CHECKING-guarded aliases) with
  still-unresolvable annotations becoming probe-eligible; Option<T>
  params flip None -> Some(synthesized) after a first-size rejection;
  foreign-arch cfgs and unsafe fns filtered at discovery instead of
  burning rebuild cycles. humanize: 0/21 -> 15/21 measured.
- **diff subcommand**: complexity-regression gate against a `scan
  --json` baseline. One-sided and ambiguity-robust: fails only when the
  most charitable new reading exceeds the old worst case; overlapping
  candidate sets warn. Catches the accidental-quadratic class that
  fixed-n benchmark gates miss.
- **Signal integrity**: runner stdout EOF is now distinguishable from a
  hang, so a crashing runner can no longer be misclassified
  `timeout_hard` and evade the crash blacklist (was a race, and a flaky
  test). Pass-through returns (`return xs`) no longer trigger the
  tracemalloc blindspot demotion (`ret_is_input`).

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
