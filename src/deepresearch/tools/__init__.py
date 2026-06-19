"""Tool definitions for DeepeResearch agents.

Tools give LLM agents the ability to interact with external systems
(e.g., web search) and incorporate real-time data into their research.
"""

from __future__ import annotations

# Existing tools — expose convenience aliases (avoid naming collision
# between the ``web_search`` module and ``web_search`` function).
from deepresearch.tools import web_search as _web_search_mod
from deepresearch.tools.cache import SearchCache
from deepresearch.tools.content_fetcher import fetch_page_content
from deepresearch.tools.parser import (
    DSMLStrategy,
    FencedBlockStrategy,
    JSONInlineStrategy,
    ParseStrategy,
    ParsedToolCall,
    ToolCallParser,
    ToolCallTagStrategy,
    XMLInvokeStrategy,
)
from deepresearch.tools.registry import ToolDef, get_registry, register_tool, resolve_tool

# Re-export existing public symbols
WEB_SEARCH_TOOL = _web_search_mod.WEB_SEARCH_TOOL
get_search_health_info = _web_search_mod.get_search_health_info
get_search_semaphore_info = _web_search_mod.get_search_semaphore_info

__all__ = [
    # Existing
    "WEB_SEARCH_TOOL",
    "get_search_health_info",
    "get_search_semaphore_info",
    # parser
    "ParsedToolCall",
    "ParseStrategy",
    "JSONInlineStrategy",
    "FencedBlockStrategy",
    "ToolCallTagStrategy",
    "XMLInvokeStrategy",
    "DSMLStrategy",
    "ToolCallParser",
    # registry
    "ToolDef",
    "register_tool",
    "resolve_tool",
    "get_registry",
    # content_fetcher
    "fetch_page_content",
    # cache
    "SearchCache",
]
