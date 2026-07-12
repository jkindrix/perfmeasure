"""The entire per-language surface the core sees."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class LanguagePlugin(ABC):
    language: str
    extensions: tuple[str, ...]

    @abstractmethod
    def claims(self, path: Path) -> bool:
        """Does this plugin handle the given file/project?"""

    @abstractmethod
    def runner_command(self, target_root: Path) -> list[str]:
        """argv for RunnerSession to spawn; the process must speak the
        protocol on stdio, starting with a hello."""
