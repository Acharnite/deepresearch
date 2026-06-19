"""Tests for SearchCache — disk-backed LRU cache and _CacheIndex."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import pytest


def _import_cache():
    """Lazy-import cache module."""
    from deepresearch.tools.cache import SearchCache, _CacheIndex, _is_current_event, _make_key

    return {
        "SearchCache": SearchCache,
        "_CacheIndex": _CacheIndex,
        "_is_current_event": _is_current_event,
        "_make_key": _make_key,
    }


class TestCacheIndex:
    """_CacheIndex — internal cache index."""

    def test_get_unknown_key(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        assert idx.get("nonexistent") is None

    def test_put_and_get(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx.put("abc123", ttl=3600)
        entry = idx.get("abc123")
        assert entry is not None
        assert entry["key"] == "abc123"
        assert entry["ttl"] == 3600
        assert "created_at" in entry
        assert "last_access" in entry

    def test_touch_updates_last_access(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx.put("abc123", ttl=3600)
        old_access = idx.get("abc123")["last_access"]
        time.sleep(0.001)
        idx.touch("abc123")
        new_access = idx.get("abc123")["last_access"]
        assert new_access > old_access

    def test_touch_missing_key(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx.touch("nonexistent")  # Should not raise

    def test_is_expired_fresh(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx.put("abc123", ttl=3600)
        assert not idx.is_expired("abc123")

    def test_is_expired_missing_key(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        assert idx.is_expired("nonexistent")

    def test_remove(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx.put("abc123", ttl=3600)
        idx.remove("abc123")
        assert idx.get("abc123") is None

    def test_size(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        assert idx.size == 0
        idx.put("k1", 3600)
        idx.put("k2", 3600)
        assert idx.size == 2

    def test_evict_lru_below_max(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx._max_entries = 10
        idx.put("k1", 3600)
        idx.put("k2", 3600)
        evicted = idx.evict_lru()
        assert evicted == []
        assert idx.size == 2

    def test_evict_lru_above_max(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx._max_entries = 2
        idx.put("k1", 3600)
        idx.put("k2", 3600)
        idx.put("k3", 3600)
        evicted = idx.evict_lru()
        assert len(evicted) == 1
        assert evicted[0] == "k1"
        assert idx.size == 2

    def test_to_dict_and_from_dict_roundtrip(self) -> None:
        C = _import_cache()
        idx = C["_CacheIndex"]()
        idx._max_entries = 100
        idx.put("k1", 3600)
        idx.put("k2", 1800)
        data = idx.to_dict()
        restored = C["_CacheIndex"].from_dict(data)
        assert restored.size == 2
        assert restored.get("k1") is not None
        assert restored.get("k2") is not None
        assert restored._max_entries == 100


class TestSearchCache:
    """SearchCache — disk-backed LRU cache."""

    @pytest.fixture
    def cache(self, tmp_path: Path) -> None:
        """Return a SearchCache with a temp directory."""
        C = _import_cache()
        return C["SearchCache"](cache_dir=tmp_path, max_entries=10)

    @pytest.mark.asyncio
    async def test_cache_miss(self, cache) -> None:
        result = await cache.get("nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_set_and_hit(self, cache) -> None:
        data = [{"title": "Test", "snippet": "Test snippet", "url": "https://example.com"}]
        await cache.set("key1", data, ttl=3600)
        result = await cache.get("key1")
        assert result is not None
        assert len(result) == 1
        assert result[0]["title"] == "Test"

    @pytest.mark.asyncio
    async def test_cache_multiple_keys(self, cache) -> None:
        await cache.set("k1", [{"title": "A"}], ttl=3600)
        await cache.set("k2", [{"title": "B"}], ttl=3600)
        r1 = await cache.get("k1")
        r2 = await cache.get("k2")
        assert r1 == [{"title": "A"}]
        assert r2 == [{"title": "B"}]

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self, cache) -> None:
        data = [{"title": "Expiring"}]
        await cache.set("exp_key", data, ttl=0)
        await asyncio.sleep(0.01)
        result = await cache.get("exp_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_lru_eviction(self) -> None:
        """Adding many entries should evict oldest and limit index size."""
        C = _import_cache()
        with tempfile.TemporaryDirectory() as td:
            cache = C["SearchCache"](cache_dir=td)
            for i in range(510):
                await cache.set(f"key_{i}", [{"title": str(i)}], ttl=3600)
            assert cache._index.size <= 500
            assert await cache.get("key_0") is None
            assert await cache.get("key_1") is None
            assert await cache.get("key_500") is not None

    @pytest.mark.asyncio
    async def test_concurrent_writes_dont_crash(self) -> None:
        """Multiple concurrent set() calls should not raise."""
        C = _import_cache()
        with tempfile.TemporaryDirectory() as td:
            cache = C["SearchCache"](cache_dir=td, max_entries=100)

            async def write(i: int) -> None:
                await cache.set(f"key_{i}", [{"title": str(i)}], ttl=3600)

            tasks = [write(i) for i in range(50)]
            await asyncio.gather(*tasks, return_exceptions=True)
            k0 = await cache.get("key_0")
            assert k0 is not None or await cache.get("key_49") is not None

    @pytest.mark.asyncio
    async def test_best_effort_corrupt_file(self, cache) -> None:
        """Corrupt cache data should return None, not crash."""
        await cache.set("key1", [{"title": "Good"}], ttl=3600)
        for f in Path(cache._cache_dir).iterdir():
            if f.name.endswith(".json") and f.name != "_index.json":
                f.write_text("corrupt json data{{{", encoding="utf-8")
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_best_effort_missing_file(self, cache) -> None:
        """If cache file is deleted after index, get returns None."""
        await cache.set("key1", [{"title": "Gone"}], ttl=3600)
        for f in Path(cache._cache_dir).iterdir():
            if f.name.endswith(".json") and f.name != "_index.json":
                f.unlink()
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_is_current_event_heuristic(self) -> None:
        C = _import_cache()
        assert C["_is_current_event"]("latest AI news")
        assert C["_is_current_event"]("breaking news today")
        assert C["_is_current_event"]("this week in tech")
        assert not C["_is_current_event"]("quantum computing basics")
        assert not C["_is_current_event"]("history of mathematics")

    def test_make_key_deterministic(self) -> None:
        C = _import_cache()
        k1 = C["_make_key"]("AI News", "searxng", 5)
        k2 = C["_make_key"]("AI News", "searxng", 5)
        assert k1 == k2
        assert len(k1) == 64

    def test_make_key_different_params(self) -> None:
        C = _import_cache()
        k1 = C["_make_key"]("AI News", "searxng", 5)
        k2 = C["_make_key"]("AI News", "searxng", 10)
        assert k1 != k2
