"""Project config: .perf-lint.toml at the target's root (or cwd).

Precedence: CLI flag > environment variable > config file > default.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

FILENAME = ".perf-lint.toml"


@dataclass
class Config:
    exclude: list[str] = field(default_factory=list)  # fnmatch globs on full path
    fail_on: str = "med"  # "high" | "med" | "never"
    llm_model: str | None = None
    llm_url: str | None = None


def load_config(paths: list[str]) -> Config:
    """Look for .perf-lint.toml in the first target directory, then cwd."""
    candidates = []
    if paths:
        first = paths[0] if os.path.isdir(paths[0]) else os.path.dirname(paths[0])
        candidates.append(os.path.join(first, FILENAME))
    candidates.append(os.path.join(os.getcwd(), FILENAME))
    for candidate in candidates:
        if os.path.isfile(candidate):
            with open(candidate, "rb") as f:
                data = tomllib.load(f)
            return Config(
                exclude=list(data.get("exclude", [])),
                fail_on=data.get("fail_on", "med"),
                llm_model=data.get("llm_model"),
                llm_url=data.get("llm_url"),
            )
    return Config()
