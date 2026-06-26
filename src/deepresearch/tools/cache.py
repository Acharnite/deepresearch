"""Disk-backed LRU search cache with per-query-type TTL.

Caches search results to disk as JSON files. Uses SHA-256 key hashing,
LRU eviction (max 500 entries), and TTL-based expiry on read.
Cache writes are best-effort — failures are logged but not raised.

ponytail: simple JSON file format for simplicity and debuggability.
Ceiling: no compression, no concurrent-write safety.
Upgrade path: use sqlite or redis for production workloads.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────────────

_DEFAULT_CACHE_DIR = "~/.cache/deepresearch/search_cache"
_DEFAULT_MAX_ENTRIES = 500
_DEFAULT_TTL_CURRENT_EVENTS = 300  # 5 minutes
_DEFAULT_TTL_EVERGREEN = 3600  # 1 hour

# Cache index file name (stored inside cache directory)
_INDEX_FILE = "_index.json"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_cache_dir() -> Path:
    """Return the cache directory path, respecting ``XDG_CACHE_HOME``."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".cache"
    return base / "deepresearch" / "search_cache"


def _normalize_query(query: str) -> str:
    """Normalize a query for cache key consistency."""
    return " ".join(query.strip().lower().split())


def _make_key(query: str, engine: str, max_results: int) -> str:
    """Create a SHA-256 cache key from query parameters."""
    normalized = _normalize_query(query)
    raw = "|".join([normalized, engine, str(max_results)])
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_current_event(query: str) -> bool:
    """Heuristic: check if the query looks time-sensitive."""
    keywords = {
        "today",
        "latest",
        "breaking",
        "just released",
        "this week",
        "this month",
        "this year",
        "past week",
        "past month",
        "past year",
        "recent",
        "new",
        "upcoming",
        "yesterday",
        "now",
    }
    query_lower = query.strip().lower()
    for kw in keywords:
        if kw in query_lower:
            return True
    return False


# ── Cache Index ──────────────────────────────────────────────────────────────


class _CacheIndex:
    """In-memory index of cache entries with LRU tracking.

    Serialised to JSON on disk for persistence across restarts.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}  # key → metadata
        self._max_entries: int = _DEFAULT_MAX_ENTRIES

    def get(self, key: str) -> dict[str, Any] | None:
        """Get entry metadata. Returns None if key not found."""
        return self._entries.get(key)

    def put(self, key: str, ttl: int) -> None:
        """Add or update an entry in the index."""
        self._entries[key] = {
            "key": key,
            "created_at": time.time(),
            "ttl": ttl,
            "last_access": time.time(),
        }

    def touch(self, key: str) -> None:
        """Update last_access time for LRU tracking."""
        entry = self._entries.get(key)
        if entry is not None:
            entry["last_access"] = time.time()

    def is_expired(self, key: str) -> bool:
        """Check if an entry's TTL has expired."""
        entry = self._entries.get(key)
        if entry is None:
            return True
        elapsed = time.time() - entry["created_at"]
        return elapsed > entry["ttl"]

    def evict_lru(self) -> list[str]:
        """Evict the least-recently-used entries to stay under max."""
        if len(self._entries) <= self._max_entries:
            return []
        # Sort by last_access (oldest first)
        sorted_keys = sorted(
            self._entries.keys(),
            key=lambda k: self._entries[k]["last_access"],
        )
        to_evict = sorted_keys[: len(sorted_keys) - self._max_entries]
        for key in to_evict:
            del self._entries[key]
        return to_evict

    def remove(self, key: str) -> None:
        """Remove a key from the index."""
        self._entries.pop(key, None)

    @property
    def size(self) -> int:
        return len(self._entries)

    def to_dict(self) -> dict:
        return {"max_entries": self._max_entries, "entries": dict(self._entries)}

    @classmethod
    def from_dict(cls, data: dict) -> _CacheIndex:
        index = cls()
        index._max_entries = data.get("max_entries", _DEFAULT_MAX_ENTRIES)
        index._entries = {}
        for key, meta in data.get("entries", {}).items():
            # Cast values that may have lost type in JSON round-trip
            meta["created_at"] = float(meta["created_at"])
            meta["ttl"] = int(meta["ttl"])
            meta["last_access"] = float(meta["last_access"])
            index._entries[key] = meta
        return index


# ── SearchCache ──────────────────────────────────────────────────────────────


class SearchCache:
    """Disk-backed LRU search cache with per-query-type TTL.

    Usage::

        cache = SearchCache()
        cached = await cache.get("some_key")
        if cached is None:
            results = await do_search(...)
            await cache.set("some_key", results, ttl=300)
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._cache_dir = (
            Path(cache_dir).expanduser().resolve() if cache_dir else _get_cache_dir()
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._index = _CacheIndex()
        self._index._max_entries = max_entries
        self._load_index()

    # ── Public API ───────────────────────────────────────────────────────

    async def get(self, key: str) -> list[dict] | None:
        """Retrieve cached results for *key*.

        Returns ``None`` if the key is not found, the cache file is
        missing, or the TTL has expired. Expired entries are evicted
        from the index.
        """
        async with self._lock:
            entry = self._index.get(key)
            if entry is None:
                return None

            # TTL check
            if self._index.is_expired(key):
                self._index.remove(key)
                self._prune_file(key)
                self._save_index()
                logger.debug("Cache entry '%s' expired (TTL)", key[:16])
                return None

            # File existence check
            cache_file = self._cache_dir / f"{key}.json"
            if not cache_file.exists():
                self._index.remove(key)
                self._save_index()
                return None

            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read cache file '%s': %s", cache_file.name, e)
                self._index.remove(key)
                self._save_index()
                return None

            self._index.touch(key)
            self._save_index()
            logger.debug("Cache hit for key '%s'", key[:16])
            return data.get("results")

    async def set(self, key: str, results: list[dict], ttl: int | None = None) -> None:
        """Cache results under *key* with a given *ttl* (seconds).

        If *ttl* is ``None``, it is auto-selected based on whether the
        query looks time-sensitive (5 min for current events, 1 hour
        otherwise).

        Cache write failures are logged as warnings but do not raise
        exceptions.
        """
        if ttl is None:
            ttl = (
                _DEFAULT_TTL_CURRENT_EVENTS
                if _is_current_event(key)
                else _DEFAULT_TTL_EVERGREEN
            )

        async with self._lock:
            # Write cache file
            cache_file = self._cache_dir / f"{key}.json"
            try:
                cache_file.write_text(
                    json.dumps(
                        {"key": key, "results": results, "cached_at": time.time()}
                    ),
                    encoding="utf-8",
                )
            except OSError as e:
                logger.warning(
                    "Failed to write cache file '%s': %s", cache_file.name, e
                )
                return

            # Update index
            self._index.put(key, ttl)
            evicted = self._index.evict_lru()
            for ev_key in evicted:
                self._prune_file(ev_key)
                logger.debug("Evicted cache entry '%s' (LRU)", ev_key[:16])
            self._save_index()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _cache_file(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _prune_file(self, key: str) -> None:
        """Delete a cache file for the given key (best-effort)."""
        try:
            self._cache_file(key).unlink(missing_ok=True)
        except OSError as e:
            logger.debug("Failed to prune cache file '%s': %s", key[:16], e)

    def _load_index(self) -> None:
        """Load the cache index from disk."""
        index_file = self._cache_dir / _INDEX_FILE
        if not index_file.exists():
            self._index = _CacheIndex()
            return
        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
            self._index = _CacheIndex.from_dict(data)
            logger.debug("Loaded cache index with %d entries", self._index.size)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load cache index: %s (starting fresh)", e)
            self._index = _CacheIndex()

    def _save_index(self) -> None:
        """Persist the cache index to disk (best-effort)."""
        index_file = self._cache_dir / _INDEX_FILE
        try:
            index_file.write_text(json.dumps(self._index.to_dict()), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to save cache index: %s", e)
