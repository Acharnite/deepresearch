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


# Module-level singleton.
settings_manager = SettingsManager()
