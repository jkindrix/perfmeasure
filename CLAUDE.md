# perfmeasure — agent notes

## Pre-push gate (mirrors .github/workflows/ci.yml)

Run the full mirror before every push:

```sh
uv run pytest -q && uv build && uv run python evals/harness.py
```

CI additionally pins the Python 3.9 target-interpreter floor
(`uv python install 3.9`, then `perfmeasure fn` with `--python`); run it
locally only when touching the runner:

```sh
uv run perfmeasure fn tests/fixtures/sample_target.py \
  --python "$(uv python find 3.9)" --budget 8 | grep -E 'T=O'
```

The eval gate is timing-sensitive (~180 s, quiet machine). The wild
sweep (`uv run python evals/wild.py`) is machine-local — most targets
live in this user's home directory — and is not part of CI.

## Conventions

- Gate figures in README are generated: `python evals/harness.py
  --update-readme` after any change that shifts them; never hand-edit
  between the gate markers.
- Version lives in `pyproject.toml` AND `perfmeasure/core/model.py`
  (`TOOL_VERSION`); a test fails if they drift.
- Fitter thresholds are calibrated against `evals/harness.py`, not
  vibes: any fitting change must be validated by a full gate run.
