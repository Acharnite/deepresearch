"""Tests for TOOL_REGISTRY — resolve_tool and register_tool."""

from __future__ import annotations

import pytest


def _import_registry():
    """Lazy-import registry modules."""
    from deepresearch.tools.registry import (
        ToolDef,
        get_registry,
        register_tool,
        resolve_tool,
    )

    return {
        "ToolDef": ToolDef,
        "get_registry": get_registry,
        "register_tool": register_tool,
        "resolve_tool": resolve_tool,
    }


class TestRegistry:
    """TOOL_REGISTRY — resolve_tool and register_tool."""

    def test_resolve_tool_canonical(self) -> None:
        """resolve_tool('web_search') should return a ToolDef with name 'web_search'."""
        r = _import_registry()
        tool = r["resolve_tool"]("web_search")
        assert tool is not None
        assert tool.name == "web_search"
        assert tool.handler is not None
        assert tool.schema is not None

    def test_resolve_tool_alias_search(self) -> None:
        r = _import_registry()
        tool = r["resolve_tool"]("search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_resolve_tool_alias_websearch(self) -> None:
        r = _import_registry()
        tool = r["resolve_tool"]("websearch")
        assert tool is not None
        assert tool.name == "web_search"

    def test_resolve_tool_alias_google_search(self) -> None:
        r = _import_registry()
        tool = r["resolve_tool"]("google_search")
        assert tool is not None
        assert tool.name == "web_search"

    def test_resolve_tool_unknown(self) -> None:
        r = _import_registry()
        assert r["resolve_tool"]("nonexistent_tool_xyz") is None

    def test_resolve_tool_empty_string(self) -> None:
        r = _import_registry()
        assert r["resolve_tool"]("") is None

    def test_get_registry_contains_web_search(self) -> None:
        r = _import_registry()
        reg = r["get_registry"]()
        assert "web_search" in reg
        assert reg["web_search"].name == "web_search"

    def test_register_tool_duplicate_raises(self) -> None:
        """register_tool should raise ValueError on duplicate registration."""
        r = _import_registry()
        tool = r["ToolDef"](
            name="web_search",
            aliases=[],
            handler=None,
            schema=None,
            description="test",
        )
        with pytest.raises(ValueError, match="already registered"):
            r["register_tool"](tool)

    def test_register_tool_new_tool_succeeds(self) -> None:
        """Registering a new tool should succeed."""
        r = _import_registry()
        tool = r["ToolDef"](
            name="_test_unique_tool_abc123",
            aliases=["_test_alias"],
            handler=None,
            schema=None,
            description="test tool for testing",
        )
        try:
            r["register_tool"](tool)
            resolved = r["resolve_tool"]("_test_unique_tool_abc123")
            assert resolved is not None
            assert resolved.name == "_test_unique_tool_abc123"
            alias_resolved = r["resolve_tool"]("_test_alias")
            assert alias_resolved is not None
            assert alias_resolved.name == "_test_unique_tool_abc123"
        finally:
            pass

    def test_tool_def_schema_shape(self) -> None:
        r = _import_registry()
        tool = r["resolve_tool"]("web_search")
        assert tool is not None
        assert tool.schema is not None
        assert tool.schema.get("type") == "function"
        assert "function" in tool.schema
        assert tool.schema["function"].get("name") == "web_search"

    def test_tool_def_description(self) -> None:
        r = _import_registry()
        tool = r["resolve_tool"]("web_search")
        assert tool is not None
        assert len(tool.description) > 10
