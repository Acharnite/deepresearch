"""Shared fixtures for the DeepeResearch test suite.

Patches LLMClient.generate so that the model connectivity check in
create_session() does not attempt a real API call during tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_llm_client() -> None:
    """Mock LLMClient.generate to return 'ok' for all tests.

    This prevents the model connectivity check (added in create_session)
    from making real API calls. The mock applies to all tests automatically.
    """
    with patch(
        "deepresearch.llm.client.LLMClient.generate", new_callable=AsyncMock
    ) as mock_generate:
        mock_generate.return_value = "ok"
        yield


@pytest.fixture(autouse=True)
def mock_searxng():
    """Autouse fixture that patches httpx for SearXNG so tests don't call a real instance."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"results": []}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
        yield


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
