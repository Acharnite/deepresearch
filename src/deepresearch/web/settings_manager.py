"""Settings manager for API keys and local model endpoints.

The :class:`SettingsManager` persists configuration to ``~/.deepresearch/``
and provides access to API keys and local model endpoints for the
web dashboard.
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Supported LLM providers and their environment variable names.
PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "env_var": "OPENAI_API_KEY",
        "name": "OpenAI",
        "url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "name": "Anthropic",
        "url": "https://api.anthropic.com",
    },
    "groq": {
        "env_var": "GROQ_API_KEY",
        "name": "Groq",
        "url": "https://api.groq.com/openai/v1",
    },
    "gemini": {
        "env_var": "GEMINI_API_KEY",
        "name": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com",
    },
    "cohere": {
        "env_var": "COHERE_API_KEY",
        "name": "Cohere",
        "url": "https://api.cohere.ai",
    },
    "together": {
        "env_var": "TOGETHER_API_KEY",
        "name": "Together AI",
        "url": "https://api.together.xyz/v1",
    },
    "deepseek": {
        "env_var": "DEEPSEEK_API_KEY",
        "name": "DeepSeek",
        "url": "https://api.deepseek.com",
    },
    "openrouter": {
        "env_var": "OPENROUTER_API_KEY",
        "name": "OpenRouter",
        "url": "https://openrouter.ai/api/v1",
    },
    "opencode": {
        "env_var": "OPENCODE_API_KEY",
        "name": "Opencode AI",
        "url": "https://api.opencode.ai/v1",
    },
}


class ContextWindowManager:
    """Manages per-model context window overrides.

    Stores overrides in ``~/.deepresearch/context_windows.json``.
    """

    def __init__(self) -> None:
        self._settings_dir = Path.home() / ".deepresearch"
        self._settings_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._settings_dir / "context_windows.json"

    def get_overrides(self) -> dict[str, int]:
        """Return all context window overrides as ``{model_id: token_count}``."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                if isinstance(data, dict):
                    return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read context windows: %s", e)
        return {}

    def get_override(self, model_id: str) -> int | None:
        """Return the context window override for a specific model, or None."""
        return self.get_overrides().get(model_id)

    def set_override(self, model_id: str, context_window: int) -> None:
        """Set a context window override for a model."""
        overrides = self.get_overrides()
        overrides[model_id] = context_window
        self._path.write_text(json.dumps(overrides, indent=2))
        logger.info("Context window override set for '%s': %d", model_id, context_window)

    def delete_override(self, model_id: str) -> bool:
        """Remove a context window override. Returns True if it existed."""
        overrides = self.get_overrides()
        if model_id in overrides:
            del overrides[model_id]
            self._path.write_text(json.dumps(overrides, indent=2))
            logger.info("Context window override removed for '%s'", model_id)
            return True
        return False


class SettingsManager:
    """Manages API keys and local model endpoints.

    Data is persisted to ``~/.deepresearch/``:
      - ``.env`` — API keys (one per line, ``KEY=VALUE``)
      - ``local_endpoints.json`` — Custom local model endpoints
    """

    def __init__(self) -> None:
        self._settings_dir = Path.home() / ".deepresearch"
        self._settings_dir.mkdir(parents=True, exist_ok=True)
        self._env_path = self._settings_dir / ".env"
        self._endpoints_path = self._settings_dir / "local_endpoints.json"

    # ── API Keys ──────────────────────────────────────────────────────

    def get_keys(self) -> dict[str, dict[str, Any]]:
        """Return all configured providers and whether they have keys set."""
        result: dict[str, dict[str, Any]] = {}
        for provider_id, info in PROVIDERS.items():
            key = os.environ.get(info["env_var"]) or self._get_from_file(
                info["env_var"]
            )
            preview = None
            if key:
                preview = key[:8] + "..." if len(key) > 8 else "***"
            result[provider_id] = {
                "name": info["name"],
                "provider_id": provider_id,
                "configured": bool(key),
                "has_key": bool(key),
                "key_preview": preview,
                "url": info["url"],
            }
        return result

    def set_key(self, provider: str, key: str) -> None:
        """Save an API key to the .env file and set the environment variable.

        Args:
            provider: Provider identifier (must exist in ``PROVIDERS``).
            key: The API key value.

        Raises:
            ValueError: If the provider is unknown.
        """
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        env_var = PROVIDERS[provider]["env_var"]
        os.environ[env_var] = key
        self._save_to_file(env_var, key)
        logger.info("Saved API key for provider '%s'", provider)

    def delete_key(self, provider: str) -> None:
        """Remove an API key.

        Args:
            provider: Provider identifier.
        """
        if provider in PROVIDERS:
            os.environ.pop(PROVIDERS[provider]["env_var"], None)
            self._remove_from_file(PROVIDERS[provider]["env_var"])
            logger.info("Removed API key for provider '%s'", provider)

    def _get_from_file(self, key: str) -> Optional[str]:
        """Read a single key from the .env file."""
        if not self._env_path.exists():
            return None
        for line in self._env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1]
        return None

    def _save_to_file(self, key: str, value: str) -> None:
        """Set a key in the .env file, preserving other keys."""
        lines: dict[str, str] = {}
        if self._env_path.exists():
            for line in self._env_path.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    lines[k] = v
        lines[key] = value
        self._env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in lines.items()) + "\n",
        )

    def _remove_from_file(self, key: str) -> None:
        """Remove a key from the .env file."""
        if not self._env_path.exists():
            return
        lines: list[str] = []
        for line in self._env_path.read_text().splitlines():
            if not line.startswith(f"{key}="):
                lines.append(line)
        self._env_path.write_text("\n".join(lines) + "\n")

    # ── Local Endpoints ──────────────────────────────────────────────

    def get_local_endpoints(self) -> list[dict[str, Any]]:
        """Return all saved custom local endpoints."""
        if self._endpoints_path.exists():
            try:
                return json.loads(self._endpoints_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read local endpoints: %s", e)
        return []

    def add_local_endpoint(self, data: dict[str, Any]) -> None:
        """Add a custom local endpoint.

        ``data`` should contain:
          - ``name``: A unique name for this endpoint.
          - ``endpoint``: The URL (e.g. ``http://localhost:8080/v1``).
          - ``type``: One of ``ollama``, ``llamacpp``, ``vllm``, ``openai``.
        """
        endpoints = self.get_local_endpoints()
        endpoints.append(data)
        self._endpoints_path.write_text(json.dumps(endpoints, indent=2))
        logger.info("Added local endpoint: %s", data.get("name"))

    def remove_local_endpoint(self, name: str) -> None:
        """Remove a saved local endpoint by name."""
        endpoints = [e for e in self.get_local_endpoints() if e.get("name") != name]
        self._endpoints_path.write_text(json.dumps(endpoints, indent=2))
        logger.info("Removed local endpoint: %s", name)

    # ── Scribe Model ────────────────────────────────────────────────────

    def get_scribe_model(self) -> str | None:
        """Get the saved scribe model ID, or None if not set."""
        return self._get_from_file("SCRIBE_MODEL")

    def set_scribe_model(self, model_id: str) -> None:
        """Save the scribe model ID to .env and os.environ."""
        os.environ["SCRIBE_MODEL"] = model_id
        self._save_to_file("SCRIBE_MODEL", model_id)

    def delete_scribe_model(self) -> None:
        """Remove the scribe model setting."""
        os.environ.pop("SCRIBE_MODEL", None)
        self._remove_from_file("SCRIBE_MODEL")

    # ── Search Engine Config ──────────────────────────────────────────

    def get_search_config(self) -> dict[str, Any]:
        """Return the search engine configuration.

        Reads from ``~/.deepresearch/settings.json`` ``search`` field.
        Returns defaults if not set.
        """
        defaults: dict[str, Any] = {
            "engine": "searxng",
            "searxng_url": "http://localhost:8888",
            "searxng_fallback_url": "https://searx.be",
            "searxng_engines": ["google", "bing", "duckduckgo"],
            "searxng_categories": ["general"],
            "searxng_timeout": 10,
        }
        settings_path = self._settings_dir / "settings.json"
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
                if isinstance(data, dict) and "search" in data:
                    merged = {**defaults, **data["search"]}
                    return merged
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read search config: %s", e)
        return defaults

    def set_search_config(self, config: dict[str, Any]) -> None:
        """Save the search engine configuration.

        ``config`` should contain one or more of: engine, searxng_url,
        searxng_fallback_url, searxng_engines, searxng_categories,
        searxng_timeout.
        """
        settings_path = self._settings_dir / "settings.json"
        data: dict[str, Any] = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, OSError):
                data = {}
        # Merge with existing search config
        existing = data.get("search", {})
        existing.update(config)
        data["search"] = existing
        settings_path.write_text(json.dumps(data, indent=2))
        logger.info("Search config updated: %s", config)

    # ── Max Tokens per Agent Call ──────────────────────────────────────

    def get_max_tokens(self) -> int:
        """Get the configured max tokens per agent call.

        Reads from ``~/.deepresearch/settings.json`` ``max_tokens`` field.
        Returns 4096 if not set or invalid.
        """
        settings_path = self._settings_dir / "settings.json"
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
                if isinstance(data, dict) and "max_tokens" in data:
                    val = int(data["max_tokens"])
                    if val > 0:
                        return val
            except (json.JSONDecodeError, OSError, ValueError) as e:
                logger.warning("Failed to read max_tokens setting: %s", e)
        return 4096

    def set_max_tokens(self, value: int) -> None:
        """Save the max tokens per agent call setting.

        Args:
            value: Max output tokens (must be > 0).

        Raises:
            ValueError: If value is not a positive integer.
        """
        if value < 1:
            raise ValueError("max_tokens must be >= 1")
        settings_path = self._settings_dir / "settings.json"
        data: dict[str, Any] = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text())
                if not isinstance(data, dict):
                    data = {}
            except (json.JSONDecodeError, OSError):
                data = {}
        data["max_tokens"] = value
        settings_path.write_text(json.dumps(data, indent=2))
        logger.info("Max tokens set to %d", value)


# Module-level singletons.
settings_manager = SettingsManager()
context_window_manager = ContextWindowManager()
