"""Shared fixtures for the DeepResearch test suite.

Patches LLMClient.generate so that the model connectivity check in
create_session() does not attempt a real API call during tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def mock_llm_client() -> None:
    """Mock LLMClient.generate to return 'ok'.

    This prevents the model connectivity check (added in create_session)
    from making real API calls. Tests that need this mock must request it
    explicitly via fixture parameter or @pytest.mark.usefixtures.
    """
    with patch(
        "deepresearch.llm.client.LLMClient.generate", new_callable=AsyncMock
    ) as mock_generate:
        mock_generate.return_value = "ok"
        yield


@pytest.fixture()
def mock_searxng():
    """Fixture that patches SearchChain.search so tests don't call real providers.

    Tests that need this mock must request it explicitly via fixture parameter
    or @pytest.mark.usefixtures.
    """
    from deepresearch.tools.search_chain import SearchChain

    with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []
        yield mock_search


@pytest.fixture()
def mock_ddgs():
    """Fixture that patches ddgs.DDGS for legacy backend tests.

    Only available when ddgs is installed. Tests using this fixture
    should set ``_search_engine = "ddgs"`` before calling web_search.
    """
    try:
        import ddgs  # noqa: F401
    except ImportError:
        pytest.skip("ddgs not installed")

    with patch("ddgs.DDGS") as mock_ddgs_cls:
        mock_instance = MagicMock()
        mock_instance.text.return_value = []
        mock_ddgs_cls.return_value.__enter__.return_value = mock_instance
        yield mock_ddgs_cls


@pytest.fixture()
def mock_httpx_get():
    """Mock httpx.AsyncClient.get for content_fetcher tests.

    Usage::
        mock_get, set_response = mock_httpx_get
        set_response("https://example.com", text="<html>...</html>")
        results = await fetch_page_content(["https://example.com"])
    """
    responses: dict[str, MagicMock] = {}

    def set_response(
        url: str,
        status_code: int = 200,
        text: str = "",
        json_data: dict | None = None,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.text = text
        if json_data:
            mock_resp.json = MagicMock(return_value=json_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()
        responses[url] = mock_resp
        return mock_resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:

        async def side_effect(url, **kwargs):
            if url in responses:
                return responses[url]
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body>default page</body></html>"
            return resp

        mock_get.side_effect = side_effect
        yield (mock_get, set_response)


@pytest.fixture()
def no_fetch_or_cache():
    """Disable content fetching and caching for unit tests.

    Uses ``patch`` instead of monkeypatch because the module-level
    constants are evaluated at import time, so ``setenv`` has no effect.
    """
    import deepresearch.tools.web_search as _ws_mod

    with (
        patch.object(_ws_mod, "_SEARCH_FETCH_CONTENT", False),
        patch.object(_ws_mod, "_SEARCH_CACHE_ENABLED", False),
    ):
        yield


def get_all_paths(app):
    """Recursively extract all route paths from the FastAPI app.

    After the server.py split into route modules, app.routes contains
    _IncludedRouter objects (from FastAPI's router includes), not individual
    Route objects. This helper flattens the route tree to get all paths.
    """
    paths = []
    for route in app.routes:
        if hasattr(route, "path"):
            paths.append(route.path)
        elif hasattr(route, "routes"):
            # It's a sub-router — recurse
            for sub_route in route.routes:
                if hasattr(sub_route, "path"):
                    paths.append(sub_route.path)
        elif hasattr(route, "original_router"):
            # It's an _IncludedRouter — get prefix from include_context
            prefix = ""
            if hasattr(route, "include_context") and hasattr(
                route.include_context, "prefix"
            ):
                prefix = route.include_context.prefix
            # Recurse into original_router's routes
            if hasattr(route.original_router, "routes"):
                for sub_route in route.original_router.routes:
                    if hasattr(sub_route, "path"):
                        paths.append(prefix + sub_route.path)
    return paths
