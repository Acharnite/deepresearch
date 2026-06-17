"""Prompt template loader with version metadata and substitution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class PromptTemplate:
    """Loads and renders YAML prompt templates with variable substitution.

    Usage::

        from prompts import prompts

        # Simple retrieval (no variables)
        fmt = prompts.get("research", "round_1_format")

        # With variable substitution (future use)
        prompt = prompts.get("scribe", "scribe_system")
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = prompts_dir or Path(__file__).parent
        self._cache: dict[str, dict] = {}

    def _load(self, name: str) -> dict:
        if name not in self._cache:
            path = self._dir / f"{name}.yaml"
            with open(path) as f:
                self._cache[name] = yaml.safe_load(f)
        return self._cache[name]

    def get(self, prompt_file: str, prompt_key: str, **kwargs: Any) -> str:
        """Load a prompt template and substitute variables.

        Args:
            prompt_file: YAML filename without extension (e.g. ``'research'``).
            prompt_key: Key within the YAML (e.g. ``'round_1_format'``).
            **kwargs: Variables to substitute with ``{variable}`` syntax.

        Returns:
            Rendered prompt string.
        """
        data = self._load(prompt_file)
        template: str = data[prompt_key]
        if kwargs:
            return template.format(**kwargs)
        return template

    def get_version(self, prompt_file: str) -> str:
        """Return the version string for a prompt file."""
        return self._load(prompt_file).get("version", "unknown")


# Module-level singleton for convenience.
prompts = PromptTemplate()
