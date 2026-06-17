"""YAML configuration loader for DeepeResearch.

Loads agent profiles and model definitions from YAML files,
validates them against Pydantic models, and returns typed config objects.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from deepresearch.models import AgentProfile


def _find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml or src/ marker."""
    # Start from the package location
    current = Path(__file__).resolve().parent
    for _ in range(6):  # Search up to 6 levels up
        if (current / "pyproject.toml").exists() or (
            current / ".kodehold-state"
        ).exists():
            return current
        current = current.parent
    # Fallback to the package src directory
    return Path(__file__).resolve().parent.parent.parent


class ConfigError(Exception):
    """Raised when configuration loading or validation fails."""


def load_yaml(path: str | Path) -> dict[str, Any] | list[Any]:
    """Load and parse a YAML file.

    Args:
        path: Path to the YAML file.

    Returns:
        Parsed YAML content.

    Raises:
        ConfigError: If the file cannot be read or parsed.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    if not path.is_file():
        raise ConfigError(f"Path is not a file: {path}")

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        return data
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML file {path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"Failed to read file {path}: {e}") from e


def load_agent_profiles(path: str | Path | None = None) -> list[AgentProfile]:
    """Load and validate agent profiles from a YAML file.

    Args:
        path: Path to the profiles YAML file. If None, uses the default location
              relative to the project root.

    Returns:
        List of validated AgentProfile objects.

    Raises:
        ConfigError: If loading or validation fails.
    """
    if path is None:
        root = _find_project_root()
        path = root / "src" / "profiles" / "default.yaml"

    raw = load_yaml(path)

    if not isinstance(raw, list):
        raise ConfigError(
            f"Agent profiles file must contain a YAML list, got {type(raw).__name__}"
        )

    profiles: list[AgentProfile] = []
    errors: list[str] = []

    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"Entry {i}: expected a mapping, got {type(entry).__name__}")
            continue
        try:
            profile = AgentProfile.model_validate(entry)
            profiles.append(profile)
        except ValidationError as e:
            fields = [
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            ]
            errors.append(
                f"Entry {i} ('{entry.get('id', 'unknown')}'): " + "; ".join(fields)
            )

    if errors:
        raise ConfigError("Profile validation failed:\n  " + "\n  ".join(errors))

    return profiles


def load_model_config(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load model definitions from a YAML file.

    Args:
        path: Path to the models YAML file. If None, uses the default location
              relative to the project root.

    Returns:
        List of model definition dicts.

    Raises:
        ConfigError: If loading fails.
    """
    if path is None:
        root = _find_project_root()
        path = root / "src" / "config" / "models.yaml"

    raw = load_yaml(path)

    if not isinstance(raw, dict) or "models" not in raw:
        raise ConfigError(
            f"Model config file must contain a 'models' key at top level, "
            f"got keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__}"
        )

    models = raw["models"]
    if not isinstance(models, list):
        raise ConfigError(f"'models' must be a list, got {type(models).__name__}")

    # Basic validation
    for i, m in enumerate(models):
        if not isinstance(m, dict):
            raise ConfigError(
                f"Model entry {i}: expected a mapping, got {type(m).__name__}"
            )
        if "id" not in m:
            raise ConfigError(f"Model entry {i}: missing required field 'id'")

    return models


def validate_profiles(profiles: list[AgentProfile]) -> list[str]:
    """Comprehensive validation of agent profiles.

    Checks:
      - All fields are non-empty (string fields).
      - Temperature is within 0.0–2.0 range (Pydantic enforces 0.0–1.0
        by default, but we allow up to 2.0 here for flexibility).
      - Profile IDs are unique.

    Returns:
        List of error messages (empty if valid).
    """
    errors: list[str] = []
    seen_ids: set[str] = set()

    for i, profile in enumerate(profiles):
        # Non-empty string-field check.
        for field_name in (
            "id",
            "name",
            "persona_prompt",
            "methodology",
            "knowledge_base",
            "bias_mitigation",
            "voice",
        ):
            value = getattr(profile, field_name, "")
            if not value or not value.strip():
                errors.append(
                    f"Profile {i} ('{profile.id}'): field '{field_name}' is empty"
                )

        # Temperature range (allow 0.0–2.0, even though Pydantic default is 0.0–1.0).
        if not (0.0 <= profile.temperature <= 2.0):
            errors.append(
                f"Profile {i} ('{profile.id}'): temperature {profile.temperature} "
                f"is out of range (0.0–2.0)"
            )

        # Duplicate ID check.
        if profile.id in seen_ids:
            errors.append(f"Profile {i} ('{profile.id}'): duplicate profile ID")
        seen_ids.add(profile.id)

    return errors


def validate_model_configs(models: list[dict[str, Any]]) -> list[str]:
    """Comprehensive validation of model configurations.

    Checks:
      - Each entry has a non-empty ``id`` field.
      - Model IDs match a known pattern (``provider/model`` or ``model-name``).
      - No duplicate model IDs.

    Returns:
        List of error messages (empty if valid).
    """
    import re

    errors: list[str] = []
    seen_ids: set[str] = set()

    for i, model in enumerate(models):
        model_id = model.get("id", "")
        if not model_id or not model_id.strip():
            errors.append(f"Model entry {i}: missing or empty 'id' field")

        # Check model ID format — allow provider/model or simple name.
        if model_id and not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-/]+$", model_id):
            errors.append(f"Model entry {i} ('{model_id}'): ID has unexpected format")

        if model_id in seen_ids:
            errors.append(f"Model entry {i} ('{model_id}'): duplicate model ID")
        seen_ids.add(model_id)

    return errors


def validate_all(
    profiles: list[AgentProfile] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Run all validations and return a combined list of errors.

    Args:
        profiles: Optional list of agent profiles to validate.
        models: Optional list of model configs to validate.

    Returns:
        List of all validation error messages.  Empty list = all valid.
    """
    errors: list[str] = []
    if profiles is not None:
        errors.extend(validate_profiles(profiles))
    if models is not None:
        errors.extend(validate_model_configs(models))
    return errors


def resolve_config_path(path: str | None, default_rel: str) -> Path:
    """Resolve a config file path.

    If path is None, returns the default location relative to the project root.
    Otherwise, resolves relative to CWD or absolute.

    Args:
        path: User-provided path or None.
        default_rel: Default relative path from project root.

    Returns:
        Resolved Path.
    """
    if path is not None:
        return Path(path).resolve()
    return _find_project_root() / default_rel
