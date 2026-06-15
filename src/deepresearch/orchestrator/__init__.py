"""Orchestrator package — Central coordinator for DeepResearch sessions."""

from deepresearch.orchestrator.orchestrator import Orchestrator, MAX_SESSION_DURATION
from deepresearch.config import ConfigError

__all__ = ["Orchestrator", "ConfigError", "MAX_SESSION_DURATION"]
