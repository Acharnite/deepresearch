"""Shared fixtures for the DeepeResearch test suite.

Patches LLMClient.generate so that the model connectivity check in
create_session() does not attempt a real API call during tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_llm_client() -> None:
    """Mock LLMClient.generate to return 'ok' for all tests.

    This prevents the model connectivity check (added in create_session)
    from making real API calls. The mock applies to all tests automatically.
    """
    with patch("deepresearch.llm.client.LLMClient.generate", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = "ok"
        yield
