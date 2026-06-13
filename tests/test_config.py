"""Tests for YAML configuration loading and validation."""

import tempfile
from pathlib import Path

import pytest

from deepresearch.config import (
    ConfigError,
    load_agent_profiles,
    load_model_config,
    load_yaml,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def valid_profiles_yaml() -> str:
    return """
- id: test-agent
  name: "Test Agent"
  emoji: "🧪"
  persona_prompt: "You are a test agent."
  methodology: "Test methodology."
  knowledge_base: "Test knowledge."
  bias_mitigation: "Test bias."
  voice: "Test voice."
  temperature: 0.5
"""


@pytest.fixture
def valid_models_yaml() -> str:
    return """
models:
  - id: gpt-4o
    provider: openai
    display_name: GPT-4o
    default: true
  - id: gpt-4o-mini
    provider: openai
    display_name: GPT-4o Mini
  - id: claude-sonnet-4-20250514
    provider: anthropic
    display_name: Claude Sonnet 4
"""


# ─── load_yaml Tests ─────────────────────────────────────────────────────────


class TestLoadYaml:
    def test_load_valid_file(self, valid_profiles_yaml):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(valid_profiles_yaml)
            tmp_path = f.name

        try:
            data = load_yaml(tmp_path)
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["id"] == "test-agent"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_file(self):
        with pytest.raises(ConfigError, match="not found"):
            load_yaml("/tmp/nonexistent_file_12345.yaml")

    def test_invalid_yaml(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("{{{invalid yaml: [}")
            tmp_path = f.name

        try:
            with pytest.raises(ConfigError, match="Failed to parse"):
                load_yaml(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            tmp_path = f.name

        try:
            data = load_yaml(tmp_path)
            assert data == {}
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ─── load_agent_profiles Tests ───────────────────────────────────────────────


class TestLoadAgentProfiles:
    def test_load_valid(self, valid_profiles_yaml):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(valid_profiles_yaml)
            tmp_path = f.name

        try:
            profiles = load_agent_profiles(tmp_path)
            assert len(profiles) == 1
            assert profiles[0].id == "test-agent"
            assert profiles[0].name == "Test Agent"
            assert profiles[0].temperature == 0.5
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_file_raises_error(self):
        with pytest.raises(ConfigError, match="not found"):
            load_agent_profiles("/tmp/nonexistent_profiles.yaml")

    def test_invalid_yaml_raises_error(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("{{{invalid: yaml")
            tmp_path = f.name

        try:
            with pytest.raises(ConfigError, match="Failed to parse"):
                load_agent_profiles(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_not_a_list_raises_error(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("key: value\nnested:\n  a: 1")
            tmp_path = f.name

        try:
            with pytest.raises(ConfigError, match="must contain a YAML list"):
                load_agent_profiles(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_validation_errors_reported(self):
        yaml_content = """
- id: valid-agent
  name: "Valid"
  emoji: "✓"
  persona_prompt: "P"
  methodology: "M"
  knowledge_base: "K"
  bias_mitigation: "B"
  voice: "V"
  temperature: 0.5

- id: bad-agent
  name: "Bad"
  emoji: "✗"
  # Missing required fields
  temperature: 2.0    # Out of range
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ConfigError, match="validation failed"):
                load_agent_profiles(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ─── load_model_config Tests ─────────────────────────────────────────────────


class TestLoadModelConfig:
    def test_load_valid(self, valid_models_yaml):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(valid_models_yaml)
            tmp_path = f.name

        try:
            models = load_model_config(tmp_path)
            assert len(models) == 3
            assert models[0]["id"] == "gpt-4o"
            assert models[0]["default"] is True
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_models_key(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("not_models:\n  - id: test")
            tmp_path = f.name

        try:
            with pytest.raises(ConfigError, match="must contain a 'models' key"):
                load_model_config(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_missing_file(self):
        with pytest.raises(ConfigError, match="not found"):
            load_model_config("/tmp/nonexistent_models.yaml")
