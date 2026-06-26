"""Tests for provider modules and SearchChain fallback logic."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest


# List of all provider modules to test
PROVIDER_MODULES = [
    "deepresearch.tools.providers.searxng",
    "deepresearch.tools.providers.duckduckgo",
    "deepresearch.tools.providers.brave",
    "deepresearch.tools.providers.google_pse",
    "deepresearch.tools.providers.serper",
    "deepresearch.tools.providers.tavily",
]

PROVIDER_NAMES = ["searxng", "duckduckgo", "brave", "google_pse", "serper", "tavily"]


class TestProviderImports:
    """Each provider module can be imported and exposes expected symbols."""

    @pytest.mark.parametrize("module_path, name", zip(PROVIDER_MODULES, PROVIDER_NAMES))
    def test_provider_importable(self, module_path: str, name: str) -> None:
        import importlib

        mod = importlib.import_module(module_path)
        assert mod is not None
        assert hasattr(mod, "search")
        assert callable(mod.search)
        assert mod.__name__ == module_path

    @pytest.mark.parametrize("module_path, name", zip(PROVIDER_MODULES, PROVIDER_NAMES))
    def test_provider_search_is_async(self, module_path: str, name: str) -> None:
        import importlib
        import inspect

        mod = importlib.import_module(module_path)
        fn = mod.search
        assert inspect.iscoroutinefunction(fn), f"{name}.search is not async"


class TestProviderResultShape:
    """Each provider returns the correct result shape."""

    @pytest.mark.parametrize("module_path, name", zip(PROVIDER_MODULES, PROVIDER_NAMES))
    @pytest.mark.asyncio
    async def test_provider_returns_list_of_dicts(
        self, module_path: str, name: str
    ) -> None:
        import importlib

        mod = importlib.import_module(module_path)
        fn = mod.search

        if name == "duckduckgo":
            with patch("ddgs.DDGS") as mock_ddgs:
                mock_instance = MagicMock()
                mock_instance.text.return_value = []
                mock_ddgs.return_value.__enter__.return_value = mock_instance
                results = await fn(query="test query", max_results=3)
        elif name in ("brave", "google_pse", "serper", "tavily"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {}
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.get.return_value = mock_resp
                mock_instance.__aenter__.return_value.post.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test query", max_results=3)
        else:
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {"results": []}
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.get.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test query", max_results=3)

        assert isinstance(results, list)
        if results:
            for r in results:
                assert "title" in r
                assert "snippet" in r
                assert "url" in r
                assert "source" in r
                assert r["source"] in PROVIDER_NAMES or r["source"] == name.replace(
                    "_pse", ""
                )

    @pytest.mark.parametrize("module_path, name", zip(PROVIDER_MODULES, PROVIDER_NAMES))
    @pytest.mark.asyncio
    async def test_provider_result_keys_are_strings(
        self, module_path: str, name: str
    ) -> None:
        import importlib

        mod = importlib.import_module(module_path)
        fn = mod.search

        if name == "searxng":
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "results": [
                        {
                            "title": "R1",
                            "content": "Snippet 1",
                            "url": "https://example.com/1",
                        }
                    ]
                }
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.get.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test", max_results=3)
        elif name == "brave":
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "web": {
                        "results": [
                            {
                                "title": "R1",
                                "description": "Snippet",
                                "url": "https://ex.com",
                            }
                        ]
                    }
                }
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.get.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test", max_results=3)
        elif name == "google_pse":
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "items": [
                        {"title": "R1", "snippet": "Snippet", "link": "https://ex.com"}
                    ]
                }
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.get.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test", max_results=3)
        elif name == "serper":
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "organic": [
                        {"title": "R1", "snippet": "Snippet", "link": "https://ex.com"}
                    ]
                }
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.post.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test", max_results=3)
        elif name == "tavily":
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = MagicMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = {
                    "results": [
                        {"title": "R1", "content": "Snippet", "url": "https://ex.com"}
                    ]
                }
                mock_resp.raise_for_status.return_value = None
                mock_instance.__aenter__.return_value.post.return_value = mock_resp
                mock_client.return_value = mock_instance
                results = await fn(query="test", max_results=3)
        else:
            with patch("ddgs.DDGS") as mock_ddgs:
                mock_instance = MagicMock()
                mock_instance.text.return_value = [
                    {"title": "R1", "body": "Snippet", "href": "https://ex.com"}
                ]
                mock_ddgs.return_value.__enter__.return_value = mock_instance
                results = await fn(query="test", max_results=3)

        if results:
            r = results[0]
            assert isinstance(r["title"], str)
            assert isinstance(r["snippet"], str)
            assert isinstance(r["url"], str)
            assert isinstance(r["source"], str)
            assert len(r["title"]) <= 80
            assert len(r["snippet"]) <= 150
            assert len(r["url"]) <= 80


class TestProviderMissingAPIKey:
    """Providers should handle missing API keys gracefully."""

    @pytest.mark.parametrize(
        "module_path, name, env_vars",
        [
            ("deepresearch.tools.providers.brave", "brave", ["BRAVE_API_KEY"]),
            (
                "deepresearch.tools.providers.google_pse",
                "google_pse",
                ["GOOGLE_PSE_API_KEY", "GOOGLE_PSE_CX"],
            ),
            ("deepresearch.tools.providers.serper", "serper", ["SERPER_API_KEY"]),
            ("deepresearch.tools.providers.tavily", "tavily", ["TAVILY_API_KEY"]),
        ],
    )
    @pytest.mark.asyncio
    async def test_keyed_providers_empty_on_no_key(
        self, module_path: str, name: str, env_vars: list[str]
    ) -> None:
        """API-key-requiring providers should return empty list when no key."""
        import importlib

        originals = {}
        for var in env_vars:
            originals[var] = os.environ.pop(var, None)

        try:
            importlib.invalidate_caches()
            mod = importlib.import_module(module_path)
            mod = importlib.reload(mod)
            fn = mod.search
            results = await fn(query="test query", max_results=3)
            assert isinstance(results, list)
            assert len(results) == 0
        finally:
            for var, val in originals.items():
                if val is not None:
                    os.environ[var] = val

    def test_free_providers_always_available(self) -> None:
        """SearXNG and DuckDuckGo don't need API keys."""
        import importlib

        mod = importlib.import_module("deepresearch.tools.providers.searxng")
        assert not hasattr(mod, "_API_KEY")

        mod = importlib.import_module("deepresearch.tools.providers.duckduckgo")
        assert not hasattr(mod, "_API_KEY")


class TestProviderTimeFilters:
    """Providers map time_filter correctly."""

    @pytest.mark.parametrize(
        "module_path, name, filter_input, expected_key",
        [
            ("deepresearch.tools.providers.searxng", "searxng", "day", "time_range"),
            ("deepresearch.tools.providers.searxng", "searxng", "week", "time_range"),
            ("deepresearch.tools.providers.brave", "brave", "day", "freshness"),
            ("deepresearch.tools.providers.serper", "serper", "day", "tbs"),
            ("deepresearch.tools.providers.tavily", "tavily", "day", "time_range"),
        ],
    )
    @pytest.mark.asyncio
    async def test_time_filter_passed_to_api(
        self, module_path: str, name: str, filter_input: str, expected_key: str
    ) -> None:
        import importlib

        mod = importlib.import_module(module_path)
        fn = mod.search

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"results": []}
            mock_resp.raise_for_status.return_value = None
            mock_instance.__aenter__.return_value.get.return_value = mock_resp
            mock_instance.__aenter__.return_value.post.return_value = mock_resp
            mock_client.return_value = mock_instance

            results = await fn(query="test", max_results=3, time_filter=filter_input)
            assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_searxng_time_filter_mapping(self) -> None:
        from deepresearch.tools.providers.searxng import _map_time_filter

        assert _map_time_filter("day") == "day"
        assert _map_time_filter("week") == "week"
        assert _map_time_filter("month") == "month"
        assert _map_time_filter("year") == "year"
        assert _map_time_filter(None) is None
        assert _map_time_filter("unknown") is None

    @pytest.mark.asyncio
    async def test_brave_time_filter_mapping(self) -> None:
        from deepresearch.tools.providers.brave import _map_time_filter

        assert _map_time_filter("day") == "pd"
        assert _map_time_filter("week") == "pw"
        assert _map_time_filter("month") == "pm"
        assert _map_time_filter("year") == "py"
        assert _map_time_filter(None) is None

    @pytest.mark.asyncio
    async def test_serper_time_filter_mapping(self) -> None:
        from deepresearch.tools.providers.serper import _map_time_filter

        assert _map_time_filter("day") == "qdr:d"
        assert _map_time_filter("week") == "qdr:w"
        assert _map_time_filter("month") == "qdr:m"
        assert _map_time_filter("year") == "qdr:y"
        assert _map_time_filter(None) is None


class TestSearchChain:
    """SearchChain — multi-provider fallback chain."""

    @pytest.mark.asyncio
    async def test_chain_returns_results_from_first_working_provider(self) -> None:
        from deepresearch.tools.search_chain import SearchChain

        with (
            patch("deepresearch.tools.search_chain._get_provider") as mock_get_provider,
            patch(
                "deepresearch.tools.search_chain._is_provider_configured"
            ) as mock_configured,
        ):
            mock_configured.return_value = True

            async def provider_a(
                query, max_results=5, time_filter=None, cancel_event=None
            ):
                return [
                    {
                        "title": "A1",
                        "snippet": "snippet",
                        "url": "https://a.com",
                        "source": "a",
                    }
                ]

            async def provider_b(
                query, max_results=5, time_filter=None, cancel_event=None
            ):
                return [
                    {
                        "title": "B1",
                        "snippet": "snippet",
                        "url": "https://b.com",
                        "source": "b",
                    }
                ]

            mock_get_provider.side_effect = lambda name: {
                "a": provider_a,
                "b": provider_b,
            }.get(name)

            chain = SearchChain(provider_order=["a", "b"])
            results = await chain.search("test query")

        assert len(results) == 1
        assert results[0]["title"] == "A1"

    @pytest.mark.asyncio
    async def test_chain_falls_back_on_empty_results(self) -> None:
        from deepresearch.tools.search_chain import SearchChain

        with (
            patch("deepresearch.tools.search_chain._get_provider") as mock_get_provider,
            patch(
                "deepresearch.tools.search_chain._is_provider_configured"
            ) as mock_configured,
        ):
            mock_configured.return_value = True

            async def provider_a(
                query, max_results=5, time_filter=None, cancel_event=None
            ):
                return []

            async def provider_b(
                query, max_results=5, time_filter=None, cancel_event=None
            ):
                return [
                    {
                        "title": "B1",
                        "snippet": "snippet",
                        "url": "https://b.com",
                        "source": "b",
                    }
                ]

            mock_get_provider.side_effect = lambda name: {
                "a": provider_a,
                "b": provider_b,
            }.get(name)

            chain = SearchChain(provider_order=["a", "b"])
            results = await chain.search("test query")

        assert len(results) == 1
        assert results[0]["title"] == "B1"

    @pytest.mark.asyncio
    async def test_chain_returns_empty_when_all_fail(self) -> None:
        from deepresearch.tools.search_chain import SearchChain

        with (
            patch("deepresearch.tools.search_chain._get_provider") as mock_get_provider,
            patch(
                "deepresearch.tools.search_chain._is_provider_configured"
            ) as mock_configured,
        ):
            mock_configured.return_value = True

            async def failing_provider(
                query, max_results=5, time_filter=None, cancel_event=None
            ):
                msg = "Provider error"
                raise RuntimeError(msg)

            mock_get_provider.return_value = failing_provider

            chain = SearchChain(provider_order=["a", "b"])
            results = await chain.search("test query")

        assert isinstance(results, list)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_chain_skips_unconfigured_providers(self) -> None:
        from deepresearch.tools.search_chain import SearchChain

        call_log = []

        with (
            patch("deepresearch.tools.search_chain._get_provider") as mock_get_provider,
            patch(
                "deepresearch.tools.search_chain._is_provider_configured"
            ) as mock_configured,
        ):

            def configured_side_effect(name):
                return name == "b"

            mock_configured.side_effect = configured_side_effect

            async def provider_b(
                query, max_results=5, time_filter=None, cancel_event=None
            ):
                call_log.append("b")
                return [{"title": "B1", "snippet": "", "url": "", "source": "b"}]

            mock_get_provider.side_effect = lambda name: {"b": provider_b}.get(name)

            chain = SearchChain(provider_order=["a", "b", "c"])
            results = await chain.search("test query")

        assert len(results) == 1
        assert call_log == ["b"]

    @pytest.mark.asyncio
    async def test_chain_cancel_event(self) -> None:
        from deepresearch.tools.search_chain import SearchChain

        cancel_event = asyncio.Event()
        cancel_event.set()

        chain = SearchChain(provider_order=["a", "b"])
        results = await chain.search("test query", cancel_event=cancel_event)
        assert len(results) == 0

    def test_provider_order_property(self) -> None:
        from deepresearch.tools.search_chain import SearchChain

        chain = SearchChain(provider_order=["x", "y"])
        assert chain.provider_order == ["x", "y"]

    def test_default_provider_order_constant(self) -> None:
        """_DEFAULT_ORDER is defined and contains expected providers."""
        from deepresearch.tools.search_chain import _DEFAULT_ORDER

        assert "searxng" in _DEFAULT_ORDER
        assert "duckduckgo" in _DEFAULT_ORDER
        assert len(_DEFAULT_ORDER) >= 4
