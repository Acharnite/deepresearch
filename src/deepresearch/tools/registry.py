"""Tool registry with alias support.

Maps canonical tool names to ``ToolDef`` instances and provides
alias resolution for text-parsed tool calls from local models.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """Definition of a registered tool.

    Attributes:
        name: Canonical tool name.
        aliases: Alternative names that resolve to this tool.
        handler: Async callable that executes the tool.
        schema: LiteLLM-compatible tool schema dict.
        description: Human-readable description.
    """

    name: str
    aliases: list[str] = field(default_factory=list)
    handler: Callable[..., Awaitable[Any]] | None = None
    schema: dict[str, Any] | None = None
    description: str = ""


# ── Internal state ──────────────────────────────────────────────────────────

_TOOL_REGISTRY: dict[str, ToolDef] = {}
_ALIAS_TO_CANONICAL: dict[str, str] = {}
_registry_lock = threading.Lock()


# ── Public API ──────────────────────────────────────────────────────────────


def register_tool(tool: ToolDef) -> None:
    """Register a tool in the registry.

    Args:
        tool: The ``ToolDef`` to register.

    Raises:
        ValueError: If a tool with the same name is already registered.
    """
    with _registry_lock:
        if tool.name in _TOOL_REGISTRY:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        _TOOL_REGISTRY[tool.name] = tool
        for alias in tool.aliases:
            _ALIAS_TO_CANONICAL[alias] = tool.name
        logger.debug("Registered tool '%s' with aliases %s", tool.name, tool.aliases)


def resolve_tool(name: str) -> ToolDef | None:
    """Resolve a canonical ``ToolDef`` from a tool name or alias.

    Args:
        name: Tool name or alias.

    Returns:
        The ``ToolDef`` if found, or ``None`` if no tool matches.
    """
    # Direct canonical lookup first
    tool = _TOOL_REGISTRY.get(name)
    if tool is not None:
        return tool
    # Alias lookup
    canonical = _ALIAS_TO_CANONICAL.get(name)
    if canonical is not None:
        return _TOOL_REGISTRY.get(canonical)
    return None


def get_registry() -> dict[str, ToolDef]:
    """Return a copy of the current registry (canonical name → ToolDef)."""
    with _registry_lock:
        return dict(_TOOL_REGISTRY)


# ── Built-in tool registration ──────────────────────────────────────────────

# Lazy import to avoid circular dependencies; the registry is initialized
# on first module import.
_WEB_SEARCH_REGISTERED = False


def _register_builtin_tools() -> None:
    """Register built-in tools shipped with deepresearch."""
    global _WEB_SEARCH_REGISTERED

    if _WEB_SEARCH_REGISTERED:
        return

    try:
        from deepresearch.tools.web_search import WEB_SEARCH_TOOL, web_search

        # Build LiteLLM-compatible schema from WEB_SEARCH_TOOL
        schema = dict(WEB_SEARCH_TOOL)

        register_tool(
            ToolDef(
                name="web_search",
                aliases=["search", "websearch", "google_search"],
                handler=web_search,
                schema=schema,
                description="Search the web for current information on a topic.",
            )
        )
        _WEB_SEARCH_REGISTERED = True
        logger.debug("Registered built-in tools")
    except ImportError as e:
        logger.warning("Could not register built-in tools: %s", e)


# Auto-register on import
_register_builtin_tools()
