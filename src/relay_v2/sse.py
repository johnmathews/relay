"""Broadcaster — the in-process SSE fan-out (ADR-23, ADR-10).

A **passive post-commit observer**. It owns no event data and never
writes: :class:`~relay_v2.events.EventStore.append` calls
:meth:`Broadcaster.publish` *after* a row is committed and its per-run
``seq`` is known. The SSE route replays committed history from the DB
and then drains a live subscription off this fan-out — the event store
remains the single source of truth (ADR-10). A publish failure must
never break an append (the caller guards it).

Slow-consumer policy (decided in ADR-23): each subscriber gets a
**bounded** ``asyncio.Queue``. ``publish`` is non-blocking
(``put_nowait``). If a subscriber's queue is full the broadcaster
enqueues a ``CLOSED`` sentinel (best-effort, dropping the oldest item to
make room if needed) and stops feeding that subscriber. The route ends
that connection cleanly; the browser reconnects with ``Last-Event-ID``
and the replay path fills the gap with zero loss. A dropped event with a
still-open stream would be a silent, unrecoverable gap — strictly worse
than a clean close the client transparently recovers from.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

__all__ = ["Broadcaster", "CLOSED", "SUBSCRIBER_QUEUE_MAXSIZE"]

# Per-subscriber queue depth. Generous enough that a momentarily slow
# client (one render frame) is absorbed without a forced reconnect, small
# enough that a wedged client cannot pin unbounded memory.
SUBSCRIBER_QUEUE_MAXSIZE = 256

# In-band sentinel pushed into a subscriber queue to tell the route's
# generator to end the stream (slow consumer, or run terminal + drained).
CLOSED = object()


class Broadcaster:
    """Per-``run_id`` registry of subscriber queues with a non-blocking
    fan-out. Not a writer; see module docstring / ADR-23."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Any]]] = {}
        # Guards only the registry dict mutations. publish itself does no
        # awaiting (put_nowait), so the append path is never blocked on a
        # slow subscriber (ADR-10: publish must not stall the chokepoint).
        self._lock = asyncio.Lock()

    async def _register(self, run_id: str) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
        async with self._lock:
            self._subs.setdefault(run_id, set()).add(q)
        return q

    async def _unregister(self, run_id: str, q: asyncio.Queue[Any]) -> None:
        async with self._lock:
            subs = self._subs.get(run_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    del self._subs[run_id]

    @contextlib.asynccontextmanager
    async def subscribe(self, run_id: str) -> AsyncIterator[asyncio.Queue[Any]]:
        """Register a fresh bounded queue for ``run_id`` and guarantee it
        is removed on exit — including on cancellation (the ``finally``
        runs even if the consuming generator is cancelled by a client
        disconnect)."""
        q = await self._register(run_id)
        try:
            yield q
        finally:
            await self._unregister(run_id, q)

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Non-blocking fan-out of one already-committed event to every
        subscriber for ``run_id``. Never awaits a slow subscriber: on a
        full queue, evict the oldest item then push a ``CLOSED`` sentinel
        so the route ends that connection (ADR-23 slow-consumer policy).
        ``async`` only to take the registry lock; the per-queue work is
        synchronous ``*_nowait``."""
        async with self._lock:
            subs = list(self._subs.get(run_id, ()))
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop the oldest to make room for the
                # close marker, then signal close. The client reconnects
                # with Last-Event-ID and replay backfills the gap.
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(CLOSED)

    async def publish_ephemeral(
        self, run_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        """Fan-out of an EPHEMERAL frame (not persisted, no seq).
        ADR-46 Plan B — used for assistant text/thinking deltas so the
        dashboard can render an in-progress pending row as tokens
        arrive. The wire frame is id-less, named-event
        (``event: <kind>\\ndata: <data>``) so the browser preserves its
        Last-Event-ID cursor across ephemeral frames (heartbeats use
        the same shape per ADR-45). The queue carries a
        ``_ephemeral``-marked dict; the SSE drain loop discriminates
        on the marker before reading ``seq`` from a normal envelope.
        Slow-consumer behaviour is identical to :meth:`publish` —
        eviction + CLOSED sentinel; a dropped delta is recoverable
        because the canonical :class:`AssistantText` is persisted
        (ADR-18 invariant) and replay backfills it on reconnect."""
        item = {"_ephemeral": True, "_kind": kind, "data": data}
        async with self._lock:
            subs = list(self._subs.get(run_id, ()))
        for q in subs:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(CLOSED)
