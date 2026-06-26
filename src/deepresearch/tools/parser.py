"""Multi-format tool call parser supporting 5 formats.

Parses text-embedded tool calls from model output. Strategies tried in
order; ALL matches from ALL strategies are collected.

Supported formats:
  - JSON inline:  ``{"name": "...", "arguments": {...}}``
  - Fenced block: ``\x60\x60\x60tool_call\n{...}\n\x60\x60\x60``
  - [TOOL_CALL]:  ``[TOOL_CALL] name(args_json)``
  - XML <invoke>: ``<invoke><tool_name>...</tool_name><parameters>...</parameters></invoke>``
  - DSML:         ``<tool>name</tool>\n<arguments>{...}</arguments>``
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedToolCall:
    """A single tool call parsed from model output text."""

    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    source: str = ""


class _ParseStrategy(ABC):
    """Base class for a tool call parsing strategy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this strategy."""

    @abstractmethod
    def parse(self, text: str) -> list[ParsedToolCall]:
        """Find all tool calls in *text* matching this strategy."""


# ── Helpers ─────────────────────────────────────────────────────────────────


def _find_json_objects(text: str) -> list[str]:
    """Extract top-level JSON objects from *text*, handling nested braces.

    ponytail: lightweight brace-matching as an alternative to
    ``json.JSONDecoder.raw_decode()``.  Ceiling: can match braces inside
    string values.  Upgrade path: use ``raw_decode()`` if false positives
    become a problem.  Ceiling acceptable: rare in LLM output.
    """
    objs: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            depth = 0
            start = i
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        objs.append(text[start : i + 1])
                        break
                i += 1
        i += 1
    return objs


def _try_parse_json_obj(obj_str: str) -> dict[str, Any] | None:
    """Try to parse a JSON string into a dict. Returns None on failure."""
    try:
        obj = json.loads(obj_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


# ── Strategy 1: JSON inline ─────────────────────────────────────────────────


class _JSONInlineStrategy(_ParseStrategy):
    """Match inline JSON tool calls: ``{"name": "...", "arguments": {...}}``."""

    @property
    def name(self) -> str:
        return "json_inline"

    def parse(self, text: str) -> list[ParsedToolCall]:
        results: list[ParsedToolCall] = []
        for obj_str in _find_json_objects(text):
            obj = _try_parse_json_obj(obj_str)
            if obj is None:
                continue
            name = obj.get("name")
            arguments = obj.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                continue
            results.append(
                ParsedToolCall(
                    call_id=str(uuid.uuid4()),
                    name=name,
                    arguments=arguments,
                    source=self.name,
                )
            )
        return results


# ── Strategy 2: Fenced block ────────────────────────────────────────────────


class _FencedBlockStrategy(_ParseStrategy):
    r"""Match fenced code blocks with ``tool_call`` language tag.

    Pattern::

        ```tool_call
        {"name": "web_search", "arguments": {...}}
        ```
    """

    @property
    def name(self) -> str:
        return "fenced_block"

    _RE = re.compile(
        r"```tool_call\s*\n(.*?)```",
        re.DOTALL,
    )

    def parse(self, text: str) -> list[ParsedToolCall]:
        results: list[ParsedToolCall] = []
        for match in self._RE.finditer(text):
            block_content = match.group(1).strip()
            if not block_content:
                continue
            # Try parsing the block content as a single JSON object or as a JSON
            # object with "name" and "arguments" keys
            obj = _try_parse_json_obj(block_content)
            if obj is not None:
                name = obj.get("name")
                arguments = obj.get("arguments")
                if isinstance(name, str) and isinstance(arguments, dict):
                    results.append(
                        ParsedToolCall(
                            call_id=str(uuid.uuid4()),
                            name=name,
                            arguments=arguments,
                            source=self.name,
                        )
                    )
                    continue
            # Fallback: try to find JSON objects within the block
            for obj_str in _find_json_objects(block_content):
                obj = _try_parse_json_obj(obj_str)
                if obj is None:
                    continue
                name = obj.get("name")
                arguments = obj.get("arguments")
                if isinstance(name, str) and isinstance(arguments, dict):
                    results.append(
                        ParsedToolCall(
                            call_id=str(uuid.uuid4()),
                            name=name,
                            arguments=arguments,
                            source=self.name,
                        )
                    )
        return results


# ── Strategy 3: [TOOL_CALL] tag ─────────────────────────────────────────────


class _ToolCallTagStrategy(_ParseStrategy):
    r"""Match ``[TOOL_CALL]`` tags.

    Pattern::

        [TOOL_CALL] web_search({"query": "AI"})
    """

    @property
    def name(self) -> str:
        return "tool_call_tag"

    _RE = re.compile(
        r"\[TOOL_CALL\]\s+(\w+)\s*\((.+?)\)\s*(?:\n|$|(?=\[TOOL_CALL\]))",
        re.DOTALL,
    )

    def parse(self, text: str) -> list[ParsedToolCall]:
        results: list[ParsedToolCall] = []
        for match in self._RE.finditer(text):
            name = match.group(1)
            args_str = match.group(2).strip()
            # Handle the case where the args JSON might be spread across multiple
            # lines or contain nested braces — find the matching JSON object
            args: dict[str, Any] | None = None
            obj = _try_parse_json_obj(args_str)
            if obj is not None:
                args = obj
            else:
                # Try to find a JSON object within the args string
                for obj_str in _find_json_objects(args_str):
                    obj = _try_parse_json_obj(obj_str)
                    if obj is not None:
                        args = obj
                        break
            if args is None:
                logger.debug(
                    "Could not parse [TOOL_CALL] args for '%s': %s", name, args_str[:50]
                )
                continue
            results.append(
                ParsedToolCall(
                    call_id=str(uuid.uuid4()),
                    name=name,
                    arguments=args,
                    source=self.name,
                )
            )
        return results


# ── Strategy 4: XML <invoke> ────────────────────────────────────────────────


class _XMLInvokeStrategy(_ParseStrategy):
    r"""Match XML ``<invoke>`` blocks.

    Pattern::

        <invoke>
          <tool_name>web_search</tool_name>
          <parameters>{"query": "AI"}</parameters>
        </invoke>
    """

    @property
    def name(self) -> str:
        return "xml_invoke"

    _RE = re.compile(
        r"<invoke>(.*?)</invoke>",
        re.DOTALL,
    )

    _TOOL_NAME_RE = re.compile(r"<tool_name>(.*?)</tool_name>", re.DOTALL)
    _PARAMETERS_RE = re.compile(r"<parameters>(.*?)</parameters>", re.DOTALL)

    def parse(self, text: str) -> list[ParsedToolCall]:
        results: list[ParsedToolCall] = []
        for block_match in self._RE.finditer(text):
            block = block_match.group(1)
            name_match = self._TOOL_NAME_RE.search(block)
            params_match = self._PARAMETERS_RE.search(block)
            if not name_match:
                continue
            name = name_match.group(1).strip()
            if not name:
                continue
            args: dict[str, Any] = {}
            if params_match:
                params_str = params_match.group(1).strip()
                obj = _try_parse_json_obj(params_str)
                if obj is None:
                    logger.debug(
                        "Could not parse <parameters> JSON for '%s': %s",
                        name,
                        params_str[:50],
                    )
                else:
                    args = obj
            results.append(
                ParsedToolCall(
                    call_id=str(uuid.uuid4()),
                    name=name,
                    arguments=args,
                    source=self.name,
                )
            )
        return results


# ── Strategy 5: DSML (DeepSeek) ─────────────────────────────────────────────


class _DSMLStrategy(_ParseStrategy):
    r"""Match DeepSeek DSML markup.

    Pattern::

        <tool>web_search</tool>
        <arguments>{"query": "AI"}</arguments>
    """

    @property
    def name(self) -> str:
        return "dsml"

    _TOOL_RE = re.compile(r"<tool>\s*(.*?)\s*</tool>", re.DOTALL)
    _ARGUMENTS_RE = re.compile(r"<arguments>\s*(.*?)\s*</arguments>", re.DOTALL)

    def parse(self, text: str) -> list[ParsedToolCall]:
        tool_names = self._TOOL_RE.findall(text)
        argument_blocks = self._ARGUMENTS_RE.findall(text)
        if not tool_names:
            return []
        results: list[ParsedToolCall] = []
        # Pair tool names with argument blocks in order
        for i, name in enumerate(tool_names):
            name = name.strip()
            if not name:
                continue
            args: dict[str, Any] = {}
            if i < len(argument_blocks):
                params_str = argument_blocks[i].strip()
                obj = _try_parse_json_obj(params_str)
                if obj is not None:
                    args = obj
                elif params_str:
                    logger.debug(
                        "Could not parse DSML <arguments> for '%s': %s",
                        name,
                        params_str[:50],
                    )
            results.append(
                ParsedToolCall(
                    call_id=str(uuid.uuid4()),
                    name=name,
                    arguments=args,
                    source=self.name,
                )
            )
        return results


# ── ToolCallParser ──────────────────────────────────────────────────────────


class ToolCallParser:
    """Parse text-embedded tool calls from model output.

    Tries each strategy in order and collects ALL matches across all
    strategies. Duplicate tool calls (same call appearing in multiple
    formats) are not deduplicated — the caller should handle that.
    """

    def __init__(self, strategies: list[_ParseStrategy] | None = None) -> None:
        self.strategies: list[_ParseStrategy] = strategies or [
            _JSONInlineStrategy(),
            _FencedBlockStrategy(),
            _ToolCallTagStrategy(),
            _XMLInvokeStrategy(),
            _DSMLStrategy(),
        ]

    def parse(self, text: str) -> list[ParsedToolCall]:
        """Try each strategy in order; collect all matches."""
        if not text or not text.strip():
            return []

        results: list[ParsedToolCall] = []
        for strategy in self.strategies:
            try:
                matches = strategy.parse(text)
                results.extend(matches)
            except Exception:
                logger.exception("Strategy %s failed during parsing", strategy.name)

        return results
