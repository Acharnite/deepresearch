"""Async event bus for sharing orchestrator events with web subscribers.

The :class:`EventBus` provides a publish/subscribe mechanism using
``asyncio.Queue`` so that orchestrator events are streamed in real-time
to SSE (Server-Sent Events) subscribers without polling.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any


class EventBus:
    """Async event bus for sharing orchestrator events with web subscribers.

    Usage::

        from deepresearch.web.event_bus import event_bus

        # Publish (called by orchestrator._event_bus.publish)
        await event_bus.publish({"event_type": "session_start", ...})

        # Subscribe (called by SSE endpoint)
        queue = await event_bus.subscribe()
        try:
            while True:
                event = await queue.get()
                # forward to SSE client
        finally:
            await event_bus.unsubscribe(queue)
    """

    def __init__(self, history: list[dict[str, Any]] | None = None) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._lock = asyncio.Lock()
        self._history = history

    async def publish(self, event: dict[str, Any]) -> None:
        """Publish an event to all active subscribers.

        Each subscriber's queue receives the event dict.  A
        ``_server_timestamp`` field is added at publish time for
        observability.  If a ``history`` list was provided at
        construction time, the event is also appended to it.
        """
        event["_server_timestamp"] = datetime.now().isoformat()
        # Auto-record to history if wired
        if self._history is not None:
            self._history.append(event)
        async with self._lock:
            for queue in self._subscribers:
                try:
                    queue.put_nowait(event)
                except Exception:
                    pass  # Drop if a subscriber's queue is full.

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Create a new subscriber queue.

        Returns an ``asyncio.Queue`` (maxsize=1000) that will receive
        all future events published to the bus.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.append(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue so it stops receiving events."""
        async with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        """Number of currently connected subscribers."""
        return len(self._subscribers)


# Module-level singleton — imported by the orchestrator and web server.
event_bus = EventBus()
