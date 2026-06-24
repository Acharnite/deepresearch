"""Tests for ToolCallParser — all 5 formats and edge cases."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _import_parser():
    """Lazy-import parser to avoid side effects."""
    from deepresearch.tools.parser import (
        _DSMLStrategy,
        _FencedBlockStrategy,
        _JSONInlineStrategy,
        ParsedToolCall,
        ToolCallParser,
        _ToolCallTagStrategy,
        _XMLInvokeStrategy,
    )

    return {
        "DSMLStrategy": _DSMLStrategy,
        "FencedBlockStrategy": _FencedBlockStrategy,
        "JSONInlineStrategy": _JSONInlineStrategy,
        "ParsedToolCall": ParsedToolCall,
        "ToolCallParser": ToolCallParser,
        "ToolCallTagStrategy": _ToolCallTagStrategy,
        "XMLInvokeStrategy": _XMLInvokeStrategy,
    }


class TestParseFormats:
    """ToolCallParser — parametrized tests across all 5 formats."""

    @pytest.mark.parametrize(
        "source, input_text, expected_name, expected_args",
        [
            (
                "json_inline",
                '{"name": "web_search", "arguments": {"query": "AI research"}}',
                "web_search",
                {"query": "AI research"},
            ),
            (
                "json_inline_with_text",
                'Some text before\n{"name": "web_search", "arguments": {"query": "test"}}',
                "web_search",
                {"query": "test"},
            ),
            (
                "json_inline_with_text_after",
                '{"name": "web_search", "arguments": {"query": "test"}}\nAnd some after',
                "web_search",
                {"query": "test"},
            ),
            (
                "json_inline_nested",
                '{"name": "custom_tool", "arguments": {"nested": {"key": "value", "list": [1, 2]}}}',
                "custom_tool",
                {"nested": {"key": "value", "list": [1, 2]}},
            ),
            (
                "fenced_block",
                "```tool_call\n{\"name\": \"web_search\", \"arguments\": {\"query\": \"AI\"}}\n```",
                "web_search",
                {"query": "AI"},
            ),
            (
                "fenced_block_multiline",
                "```tool_call\n"
                '{"name": "web_search", "arguments": {\n'
                '  "query": "AI",\n'
                '  "max_results": 10\n'
                "}}\n"
                "```",
                "web_search",
                {"query": "AI", "max_results": 10},
            ),
            (
                "tool_call_tag",
                '[TOOL_CALL] web_search({"query": "AI"})',
                "web_search",
                {"query": "AI"},
            ),
            (
                "tool_call_tag_multi_line",
                '[TOOL_CALL] web_search({\n  "query": "AI",\n  "max_results": 5\n})',
                "web_search",
                {"query": "AI", "max_results": 5},
            ),
            (
                "tool_call_tag_with_text",
                '[TOOL_CALL] web_search({"query": "AI"})\nBased on those results...',
                "web_search",
                {"query": "AI"},
            ),
            (
                "tool_call_tag_multiple",
                '[TOOL_CALL] web_search({"query": "AI"})\n'
                '[TOOL_CALL] web_search({"query": "ML"})',
                "web_search",
                {"query": "AI"},
            ),
            (
                "xml_invoke",
                "<invoke><tool_name>web_search</tool_name>"
                "<parameters>{\"query\": \"AI\"}</parameters></invoke>",
                "web_search",
                {"query": "AI"},
            ),
            (
                "xml_invoke_no_params",
                "<invoke><tool_name>web_search</tool_name></invoke>",
                "web_search",
                {},
            ),
            (
                "xml_invoke_malformed_params",
                "<invoke><tool_name>web_search</tool_name>"
                "<parameters>{bad json}</parameters></invoke>",
                "web_search",
                {},
            ),
            (
                "dsml",
                "<tool>web_search</tool>\n<arguments>{\"query\": \"AI\"}</arguments>",
                "web_search",
                {"query": "AI"},
            ),
            (
                "dsml_no_args",
                "<tool>web_search</tool>",
                "web_search",
                {},
            ),
            (
                "dsml_multiple",
                "<tool>web_search</tool>\n"
                '<arguments>{"query": "AI"}</arguments>\n'
                "<tool>read_page</tool>\n"
                '<arguments>{"url": "https://example.com"}</arguments>',
                "web_search",
                {"query": "AI"},
            ),
        ],
    )
    def test_parse_format(
        self, source: str, input_text: str, expected_name: str, expected_args: dict
    ) -> None:
        p = _import_parser()["ToolCallParser"]()
        results = p.parse(input_text)
        assert len(results) >= 1
        assert results[0].name == expected_name
        assert results[0].arguments == expected_args


class TestParseFormatEdgeCases:
    """ToolCallParser — format-specific edge cases kept separate."""

    def test_json_inline_non_dict_json(self) -> None:
        """JSON that isn't a dict should be ignored."""
        p = _import_parser()["ToolCallParser"]()
        text = '["list", "of", "items"]'
        results = p.parse(text)
        assert len(results) == 0

    def test_json_inline_missing_arguments_key(self) -> None:
        """JSON with 'name' but no 'arguments' should be ignored."""
        p = _import_parser()["ToolCallParser"]()
        text = '{"name": "web_search", "query": "AI"}'
        results = p.parse(text)
        assert len(results) == 0

    def test_json_inline_name_not_string(self) -> None:
        """JSON where 'name' is not a string should be ignored."""
        p = _import_parser()["ToolCallParser"]()
        text = '{"name": 123, "arguments": {"query": "AI"}}'
        results = p.parse(text)
        assert len(results) == 0

    def test_fenced_block_empty(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "```tool_call\n\n```"
        results = p.parse(text)
        assert len(results) == 0

    def test_fenced_block_no_content(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "```tool_call\n```"
        results = p.parse(text)
        assert len(results) == 0

    def test_fenced_block_malformed_json(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "```tool_call\n{, , ,}\n```"
        results = p.parse(text)
        assert len(results) == 0

    def test_tool_call_tag_malformed_args(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "[TOOL_CALL] web_search(not valid json)"
        results = p.parse(text)
        assert len(results) == 0

    def test_xml_invoke_missing_tool_name(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "<invoke><parameters>{\"query\": \"AI\"}</parameters></invoke>"
        results = p.parse(text)
        assert len(results) == 0

    def test_xml_invoke_empty_tool_name(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "<invoke><tool_name>  </tool_name><parameters>{\"query\": \"AI\"}</parameters></invoke>"
        results = p.parse(text)
        assert len(results) == 0

    def test_dsml_empty_tool_name(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "<tool>  </tool>\n<arguments>{\"query\": \"AI\"}</arguments>"
        results = p.parse(text)
        assert len(results) == 0

    def test_dsml_no_tool_tags(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "Just some text with no DSML tags here."
        results = p.parse(text)
        assert len(results) == 0


class TestParserEdgeCases:
    """ToolCallParser — edge cases across all formats."""

    def test_empty_text(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        assert p.parse("") == []

    def test_whitespace_only(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        assert p.parse("   \n  \t  ") == []

    def test_no_tool_calls(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        assert p.parse("Just a regular sentence with no tool calls.") == []

    def test_malformed_json_graceful(self) -> None:
        p = _import_parser()["ToolCallParser"]()
        text = "{badly formed json with no structure}"
        results = p.parse(text)
        assert len(results) == 0

    def test_strategy_exception_isolation(self) -> None:
        """If one strategy raises, others should still work."""
        p = _import_parser()["ToolCallParser"]()
        bad = MagicMock()
        bad.name = "bad_strategy"
        bad.parse.side_effect = RuntimeError("boom")
        p.strategies = [bad, _import_parser()["JSONInlineStrategy"]()]
        text = '{"name": "web_search", "arguments": {"query": "AI"}}'
        results = p.parse(text)
        assert len(results) == 1
        assert results[0].name == "web_search"

    def test_inline_json_with_extra_braces(self) -> None:
        """JSON object inside other text with braces."""
        p = _import_parser()["ToolCallParser"]()
        text = 'Here is a {"name": "web_search", "arguments": {"query": "test"}} call'
        results = p.parse(text)
        assert len(results) == 1
        assert results[0].name == "web_search"

    def test_multiple_different_formats_in_one_text(self) -> None:
        """The parser should return ALL tool calls from all strategies."""
        p = _import_parser()["ToolCallParser"]()
        text = (
            'Some JSON inline: {"name": "tool_a", "arguments": {"x": 1}}\n'
            "And a fenced block:\n"
            "```tool_call\n"
            '{"name": "tool_b", "arguments": {"y": 2}}\n'
            "```\n"
            "And a tag:\n"
            '[TOOL_CALL] tool_c({"z": 3})'
        )
        results = p.parse(text)
        assert len(results) >= 3
        names = {r.name for r in results}
        assert "tool_a" in names
        assert "tool_b" in names
        assert "tool_c" in names

    def test_parsed_tool_call_defaults(self) -> None:
        """ParsedToolCall should have sensible defaults."""
        ParsedToolCall = _import_parser()["ParsedToolCall"]
        tc = ParsedToolCall()
        assert tc.name == ""
        assert tc.arguments == {}
        assert tc.source == ""
        assert isinstance(tc.call_id, str)
        assert len(tc.call_id) > 0
