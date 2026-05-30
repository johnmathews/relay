"""SSE event-stream router (spec.md §7, §9.3; ADR-10, ADR-23).

``GET /api/events/{run_id}`` is a **passive tail** of the event store.
It never writes. The streaming logic lives in :func:`sse_event_stream`
(a plain async generator) so it is unit-testable without an HTTP server;
the route is a thin wrapper.

Correctness contract (ADR-23):

* **Finished run.** Stream the historical events with ``seq >
  last_event_id`` paginated, then EOF (the browser stops reconnecting on
  a clean close of a finished run). If a finished run has *zero* events
  at/after ``Last-Event-ID`` the route returns a real ``204 No Content``
  *before* starting the stream — you cannot send a 204 mid-stream from a
  ``StreamingResponse``, so "204 on exhaustion" is interpreted as
  "204 only when nothing is left to send; otherwise paginated history
  then EOF".
* **Live run.** Subscribe to the broadcaster *first* so no event
  committed during replay is missed, *then* replay DB history with
  ``seq > last_event_id`` tracking ``max_replayed_seq``, *then* drain the
  live subscription forwarding only ``seq > max_replayed_seq`` — the
  subscribe-before-replay ordering plus the cutover filter give the
  "no gap, no duplicate" guarantee.

This router intentionally does NOT import ``api/deps.py``: that module is
owned by another in-flight unit and importing it now would be a
cross-unit race. W5 unifies the dependency. A minimal inline accessor is
used instead.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from relay._time import to_utc_iso
from relay.api.deps import get_core
from relay.sse import CLOSED

if TYPE_CHECKING:
    from relay.core import RelayCore

router = APIRouter(prefix="/api", tags=["events"])

# Run statuses that will emit no further events. A run in one of these is
# served as paginated history then EOF (replay mode, spec.md §9.3).
# ``paused`` and ``awaiting_children`` are NOT terminal — both can
# transition back to ``running`` (pause/resume; fanout/join respectively)
# — so both are treated as live. The constant's *value* (the omission of
# both from the set) is the behaviour; do not add ``awaiting_children``
# here without re-reading ADR-34.
_TERMINAL = frozenset({"done", "failed", "cancelled", "closed"})

# Page size for the historical replay query (keeps a long run's replay
# off a single unbounded SELECT; spec.md §9.3 "paginated").
_REPLAY_PAGE = 500

# Idle heartbeat cadence (seconds): the live generator emits a named
# ``heartbeat`` frame at this interval whenever the queue is quiet.
# Lowered from the 15s "keep proxy warm" cadence to 5s so the
# dashboard's liveness widget reflects pi-is-thinking within a
# user-noticeable window (ADR-45 Plan A). Test seam — tests
# monkeypatch this to a sub-second value to avoid sleeping.
_KEEPALIVE_S = 5.0


def _frame(seq: int, kind: str, data: dict[str, Any]) -> str:
    """One SSE event. ``id:`` is the event seq so the browser sends
    ``Last-Event-ID`` on reconnect (spec.md §9.2)."""
    return (
        f"id: {seq}\n"
        f"event: {kind}\n"
        f"data: {json.dumps(data, default=str)}\n\n"
    )


def _heartbeat_frame(data: dict[str, Any]) -> str:
    """An ephemeral liveness ping (ADR-45 Plan A). Deliberately omits
    the ``id:`` line: per WHATWG SSE, a message with no ``id`` field
    leaves the browser's Last-Event-ID buffer unchanged — exactly
    what we want, since heartbeats are not persisted and bumping the
    cursor would point at a phantom DB row on reconnect.
    """
    return (
        f"event: heartbeat\n"
        f"data: {json.dumps(data, default=str)}\n\n"
    )


def _event_payload(ev: Any) -> dict[str, Any]:
    """Normalize a DB ``Event`` row to the same dict shape the
    broadcaster publishes (so replay and live frames are identical)."""
    return {
        "seq": ev.seq,
        "kind": ev.kind,
        "payload": ev.payload,
        "ts": to_utc_iso(ev.ts),
        "run_id": ev.run_id,
        "iter_id": ev.iter_id,
    }


async def _replay(
    core: RelayCore, run_id: str, after: int
) -> AsyncIterator[tuple[int, str, dict[str, Any]]]:
    """Yield committed history with ``seq > after``, paginated, seq-asc."""
    offset = 0
    while True:
        page = await core.list_events(
            run_id, after_seq=after, limit=_REPLAY_PAGE, offset=offset
        )
        if not page:
            return
        for ev in page:
            yield ev.seq, ev.kind, _event_payload(ev)
        if len(page) < _REPLAY_PAGE:
            return
        offset += _REPLAY_PAGE


async def sse_event_stream(
    core: RelayCore, run_id: str, last_event_id: int
) -> AsyncIterator[str]:
    """The streaming generator (route-independent, unit-testable).

    Precondition: the caller has already verified the run exists and, for
    a finished run with nothing to send, returned 204 instead of calling
    this. Here a finished run yields paginated history then ends; a live
    run subscribes-then-replays-then-drains with the cutover dedupe.
    """
    run = await core.get_run(run_id)
    if run is None:  # defensive; route checks first
        return

    if run.status in _TERMINAL:
        # Finished run: paginated history then EOF (no live subscription;
        # no more events will ever be appended for this run).
        async for seq, kind, data in _replay(core, run_id, last_event_id):
            yield _frame(seq, kind, data)
        return

    # Live run. Subscribe FIRST so any event committed during the replay
    # below is captured in the queue and not lost in the replay→live gap.
    async with core.broadcaster.subscribe(run_id) as queue:
        max_replayed = last_event_id
        # Track the ts of the most recently forwarded event so idle
        # heartbeats can carry "how long has pi been silent" (ADR-45).
        last_event_ts: str | None = None
        async for seq, kind, data in _replay(core, run_id, last_event_id):
            max_replayed = max(max_replayed, seq)
            last_event_ts = data.get("ts")
            yield _frame(seq, kind, data)

        # Drain the live subscription. Forward only seq > max_replayed:
        # an event committed during replay is in BOTH the replay page and
        # the queue — this filter dedupes the cutover (no gap, no dup).
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=_KEEPALIVE_S
                )
            except TimeoutError:
                # Idle: emit a named heartbeat frame (ADR-45 Plan A —
                # supersedes the bare `: keepalive` comment so the
                # dashboard can render a live "alive · last activity
                # Xs ago" indicator). Then re-check terminality so a
                # run that finished while quiet ends the stream
                # promptly.
                yield _heartbeat_frame(
                    {
                        "run_id": run_id,
                        "server_ts": to_utc_iso(_dt.datetime.now(_dt.UTC)),
                        "last_event_ts": last_event_ts,
                    }
                )
                latest = await core.get_run(run_id)
                if latest is not None and latest.status in _TERMINAL:
                    if queue.empty():
                        return
                continue

            if item is CLOSED:
                # Slow-consumer close (ADR-23): end cleanly; the client
                # reconnects with Last-Event-ID and replay backfills.
                return

            # Ephemeral frame (ADR-46 Plan B — assistant text/thinking
            # deltas). Discriminated by the ``_ephemeral`` marker
            # publish_ephemeral writes. Rendered as a id-less named
            # event so the browser preserves Last-Event-ID across
            # deltas. Does NOT count toward ``max_replayed`` /
            # ``last_event_ts`` (not a persisted event).
            if isinstance(item, dict) and item.get("_ephemeral"):
                kind = str(item.get("_kind", "ephemeral"))
                payload = item.get("data", {})
                if not isinstance(payload, dict):
                    payload = {}
                yield _heartbeat_frame(payload) if kind == "heartbeat" else (
                    f"event: {kind}\n"
                    f"data: {json.dumps(payload, default=str)}\n\n"
                )
                continue

            seq = item["seq"]
            if seq <= max_replayed:
                continue  # already delivered during replay — dedupe
            max_replayed = seq
            last_event_ts = item.get("ts")
            yield _frame(seq, item["kind"], item)
            if item["kind"] == "run_ended":
                # `run_ended` is the last event a run ever appends
                # (spec.md §3.2). Close now instead of blocking up to
                # _KEEPALIVE_S on an empty queue — the browser sees a
                # clean EOF and stops reconnecting immediately.
                return


@router.get("/events/{run_id}")
async def stream_events(run_id: str, request: Request) -> Response:
    """SSE live stream / replay for a run (spec.md §7, ADR-23)."""
    core = get_core(request)

    run = await core.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown run_id={run_id}",
        )

    # Last-Event-ID header (browser EventSource) with a ?last_event_id=
    # query fallback for non-browser clients (curl, tests) that cannot
    # easily set the header. Header wins if both are present.
    raw = request.headers.get("Last-Event-ID") or request.query_params.get(
        "last_event_id"
    )
    try:
        last = int(raw) if raw is not None else 0
    except ValueError:
        last = 0

    # 204 ONLY when a finished run has nothing at/after Last-Event-ID
    # (ADR-23). A finished run WITH events streams history then EOF.
    #
    # The ``media_type="text/event-stream"`` is load-bearing on this 204:
    # browsers' ``EventSource`` validate the response Content-Type BEFORE
    # the status code, and FastAPI's bare ``Response(204)`` defaults to
    # ``text/plain`` — which makes the browser abort the connection with
    # ``MIME type ("text/plain") that is not "text/event-stream"`` instead
    # of treating the 204 as a clean end-of-stream. Declaring the SSE
    # mime here keeps the MIME check happy; the empty body honours the
    # ``204`` semantics (Phase 9e smoke, 2026-05-22).
    if run.status in _TERMINAL:
        tail = await core.list_events(run_id, after_seq=last, limit=1)
        if not tail:
            return Response(
                status_code=status.HTTP_204_NO_CONTENT,
                media_type="text/event-stream",
            )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Disable response buffering in an nginx reverse proxy: without
        # this nginx buffers the stream and SSE events arrive in bursts
        # (or stall) instead of live (plan.md Phase 3 risk note).
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        sse_event_stream(core, run_id, last),
        media_type="text/event-stream",
        headers=headers,
    )
