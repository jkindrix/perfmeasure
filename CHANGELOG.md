# Changelog

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
