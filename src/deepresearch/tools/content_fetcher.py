"""Parallel page content fetching.

Fetches and extracts text content from URLs in parallel using
``httpx.AsyncClient``. Returns a list of result dicts with graceful
failure handling — individual URL failures return error dicts without
raising exceptions.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Module-level shared HTTP client — avoids creating one client per URL.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared module-level ``httpx.AsyncClient``."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10)
    return _client


# ── HTML tag stripping (simple regex-based, no external deps) ───────────────
# ponytail: regex-based tag stripper is intentionally simple.
# Ceiling: does not handle malformed HTML, CDATA, or comments.
# Upgrade path: use html.parser or BeautifulSoup if structure matters.

_RE_TAG = re.compile(r"<[^>]+>", re.DOTALL)
_RE_WHITESPACE = re.compile(r"\s+")
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)


def _strip_html(html: str) -> str:
    """Strip HTML tags, collapse whitespace, and return plain text."""
    # Extract and remove <style> / <script> blocks
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = _RE_TAG.sub(" ", html)
    text = _RE_WHITESPACE.sub(" ", text)
    return text.strip()


def _extract_title(html: str) -> str | None:
    """Extract the <title> tag content from HTML."""
    match = _RE_TITLE.search(html)
    if match:
        title = _RE_TAG.sub("", match.group(1))
        title = _RE_WHITESPACE.sub(" ", title).strip()
        return title if title else None
    return None


# ── Public API ──────────────────────────────────────────────────────────────


async def fetch_page_content(
    urls: list[str],
    max_concurrent: int = 5,
    max_chars: int = 2000,
) -> list[dict[str, Any]]:
    """Fetch and extract content from URLs in parallel.

    Args:
        urls: List of URLs to fetch.
        max_concurrent: Maximum number of concurrent fetches.
        max_chars: Maximum characters of text content to return per page.

    Returns:
        List of dicts with keys: ``url``, ``title``, ``content``, ``error``.
        On failure for a given URL, ``content`` and ``title`` are ``None``
        and ``error`` contains the error message.
    """
    if not urls:
        return []

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(url: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "url": url,
            "title": None,
            "content": None,
            "error": None,
        }
        async with semaphore:
            try:
                client = _get_client()
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                html = resp.text
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.debug("Failed to fetch '%s': %s", url, error_msg)
                result["error"] = error_msg
                return result

            try:
                result["title"] = _extract_title(html)
                text = _strip_html(html)
                if max_chars and len(text) > max_chars:
                    text = text[:max_chars]
                result["content"] = text
            except Exception as e:
                logger.debug("Failed to parse content from '%s': %s", url, e)
                result["error"] = f"ParseError: {e}"

            return result

    tasks = [_fetch_one(url) for url in urls]
    results = await asyncio.gather(*tasks)
    return list(results)
