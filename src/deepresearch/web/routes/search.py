"""Search engine routes."""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()


class SearchTestRequest(BaseModel):
    """Request body for POST /api/system/search/test."""

    query: str = "test search"


@router.get("/system/search")
async def get_search_status() -> JSONResponse:
    """Return search engine configuration, health, and cache stats."""
    from deepresearch.tools.web_search import get_search_health_info

    return JSONResponse(get_search_health_info())


@router.post("/system/search/test")
async def test_search_engine(req: SearchTestRequest | None = None) -> JSONResponse:
    """Probe SearXNG with a test query and return latency + result count."""
    from deepresearch.tools.web_search import _searxng_url, _searxng_timeout

    query = req.query if req else "test search"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_searxng_timeout) as client:
            resp = await client.get(
                f"{_searxng_url}/search",
                params={"q": query, "format": "json", "categories": "general"},
            )
            resp.raise_for_status()
            data = resp.json()
            latency = (time.monotonic() - t0) * 1000
            result_count = len(data.get("results", []))
            return JSONResponse(
                {
                    "status": "ok",
                    "results_count": result_count,
                    "latency_ms": round(latency, 1),
                    "engine_url": _searxng_url,
                }
            )
    except httpx.ConnectError:
        latency = (time.monotonic() - t0) * 1000
        return JSONResponse(
            {
                "status": "error",
                "results_count": 0,
                "latency_ms": round(latency, 1),
                "engine_url": _searxng_url,
                "message": f"Could not connect to SearXNG at {_searxng_url}",
            }
        )
    except httpx.TimeoutException:
        latency = (time.monotonic() - t0) * 1000
        return JSONResponse(
            {
                "status": "error",
                "results_count": 0,
                "latency_ms": round(latency, 1),
                "engine_url": _searxng_url,
                "message": f"SearXNG request timed out ({_searxng_timeout}s)",
            }
        )
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return JSONResponse(
            {
                "status": "error",
                "results_count": 0,
                "latency_ms": round(latency, 1),
                "engine_url": _searxng_url,
                "message": str(e),
            }
        )
