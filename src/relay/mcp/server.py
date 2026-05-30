"""FastMCP server: seven RelayCore-adapter tools (spec §8, ADR-27).

Each tool maps 1:1 to a :class:`~relay.core.RelayCore` method and
returns the *same* Pydantic schema the REST API uses
(:mod:`relay.api.schemas`), so MCP and REST stay consistent by
construction. Tools never touch the DB and never proxy the REST layer
(ADR-07/15) — they call ``core`` directly.

Two spec §8 signatures need a small, deliberate bridge:

* ``relay__list_runs`` / ``relay__start_run`` take a ``project_root``
  filesystem path; ``RelayCore`` keys on ``project_id``. The path is
  resolved against the registered projects' ``root_path`` (exact, then
  ``Path.resolve()``-normalised). An unknown root is a clear error.
* ``relay__tail_events`` is typed ``-> AsyncIterator[Event]`` in the
  spec, but an MCP tool result is a single value — a live async
  generator cannot be a tool return. It is implemented as a **bounded
  snapshot** of events after ``since_seq`` (a cursor the caller
  advances): the same data the SSE tail carries (ADR-23), pull-paged
  instead of pushed. Live push remains the SSE endpoint's job. This
  delta is recorded in ADR-27 and ``docs/mcp.md``.

Domain failures are raised as ``ValueError`` with a clear message; the
MCP runtime surfaces the exception to the client as a tool error. This
mirrors the REST adapters' intent (unknown entity / bad argument /
state conflict) without HTTP status codes.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from relay.api.files import (
    BINARY_SNIFF_BYTES,
    MAX_FILE_BYTES,
    SandboxViolation,
    resolve_within_sandbox,
)
from relay.api.schemas import EventOut, IterOut, RunDetailOut, RunOut
from relay.core import RelayCore


def create_mcp_server(core: RelayCore) -> FastMCP:
    """Build the relay FastMCP server bound to a shared ``core``.

    ``core`` is captured by closure — MCP tools have no request object,
    so this factory is the injection seam (it mirrors the
    ``create_app(settings, *, harness=)`` seam: the same ``core``
    instance the app lifespan builds is passed straight through, so the
    scripted-harness test path works unchanged).
    """
    # ``streamable_http_path="/"`` so the sub-app serves at its own
    # root: mounting it at ``/mcp`` then yields the endpoint ``/mcp``
    # (FastMCP's default ``/mcp`` internal path would double to
    # ``/mcp/mcp`` under ``app.mount("/mcp", ...)``). ADR-27.
    mcp: FastMCP = FastMCP("relay", streamable_http_path="/")

    async def _resolve_project_id(project_root: str) -> int:
        """Map a ``project_root`` path to a registered ``project_id``.

        Exact ``root_path`` match first, then a ``Path.resolve()``
        comparison (handles ``~``, relative, and symlinked spellings of
        the same directory). Unknown root → ``ValueError``.
        """
        raw = project_root
        try:
            wanted = Path(project_root).expanduser().resolve()
        except OSError:
            wanted = Path(project_root)
        for project in await core.list_projects():
            if project.root_path == raw:
                return project.id
            try:
                if Path(project.root_path).resolve() == wanted:
                    return project.id
            except OSError:
                continue
        raise ValueError(
            f"no registered project with root_path {project_root!r}"
        )

    @mcp.tool(name="relay__list_runs")
    async def list_runs(project_root: str | None = None) -> list[RunOut]:
        """List runs, newest first. With ``project_root``, scope to that
        registered project; without it, all runs across all projects."""
        project_id = (
            await _resolve_project_id(project_root)
            if project_root is not None
            else None
        )
        rows = await core.list_runs(project_id, include_children=True)
        return [RunOut.model_validate(r) for r in rows]

    @mcp.tool(name="relay__get_run")
    async def get_run(run_id: str) -> RunDetailOut:
        """Fetch one run with its iters. Unknown ``run_id`` → error."""
        run = await core.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        detail = RunDetailOut.model_validate(run)
        detail.iters = [
            IterOut.model_validate(i) for i in await core.list_iters(run_id)
        ]
        return detail

    @mcp.tool(name="relay__start_run")
    async def start_run(
        project_root: str, prompt: str, max_iters: int | None = None
    ) -> RunOut:
        """Start a run in the project at ``project_root`` with ``prompt``
        as the initial work-unit body. Returns the created run."""
        project_id = await _resolve_project_id(project_root)
        run_id = await core.start_run(
            project_id, prompt, max_iters=max_iters
        )
        run = await core.get_run(run_id)
        if run is None:  # pragma: no cover - just-created row must exist
            raise ValueError(f"run {run_id} vanished after creation")
        return RunOut.model_validate(run)

    @mcp.tool(name="relay__create_chat")
    async def create_chat(project_root: str) -> RunOut:
        """Create a chat-mode run in the project at ``project_root`` (W1).

        Chats use pi's native multi-turn model: the run starts empty and
        the operator types messages via the dashboard or
        ``relay__pause_response``. Returns the created chat run."""
        project_id = await _resolve_project_id(project_root)
        chat_id = await core.start_chat(project_id)
        run = await core.get_run(chat_id)
        if run is None:  # pragma: no cover - just-created row must exist
            raise ValueError(f"chat {chat_id} vanished after creation")
        return RunOut.model_validate(run)

    @mcp.tool(name="relay__list_chats")
    async def list_chats(project_root: str | None = None) -> list[RunOut]:
        """List chat-mode runs, newest first (W1). With ``project_root``,
        scope to that registered project; without it, all chats across
        all projects. Excludes task-mode runs."""
        project_id = (
            await _resolve_project_id(project_root)
            if project_root is not None
            else None
        )
        rows = await core.list_runs(
            project_id, include_children=True, mode="chat"
        )
        return [RunOut.model_validate(r) for r in rows]

    @mcp.tool(name="relay__cancel_run")
    async def cancel_run(run_id: str) -> RunOut:
        """Request cancellation of a run; returns its current state.
        Unknown ``run_id`` → error (vs. REST's idempotent no-op, an MCP
        caller benefits from an explicit unknown-run signal)."""
        if await core.get_run(run_id) is None:
            raise ValueError(f"unknown run {run_id}")
        await core.cancel_run(run_id)
        updated = await core.get_run(run_id)
        if updated is None:  # pragma: no cover - existed a line above
            raise ValueError(f"unknown run {run_id}")
        return RunOut.model_validate(updated)

    @mcp.tool(name="relay__pause_response")
    async def pause_response(run_id: str, answer: str) -> RunOut:
        """Answer a paused run's question and resume it. Errors if the
        run is not paused or is already running (RelayCore guards)."""
        await core.resume_run(run_id, answer)
        updated = await core.get_run(run_id)
        if updated is None:
            raise ValueError(f"unknown run {run_id}")
        return RunOut.model_validate(updated)

    @mcp.tool(name="relay__tail_events")
    async def tail_events(run_id: str, since_seq: int = 0) -> list[EventOut]:
        """Bounded snapshot of a run's events with ``seq > since_seq``,
        oldest first. Poll with the last returned ``seq`` as the next
        ``since_seq`` to tail. (Live push is the SSE endpoint; ADR-27
        explains why this is a snapshot, not an async iterator.)"""
        if await core.get_run(run_id) is None:
            raise ValueError(f"unknown run {run_id}")
        rows = await core.list_events(run_id, after_seq=since_seq)
        return [EventOut.model_validate(e) for e in rows]

    @mcp.tool(name="relay__read_artifact")
    async def read_artifact(run_id: str, path: str) -> str:
        """Read a text artifact from a run's data dir
        (``<project_root>/.relay/runs/<run_id>/``), sandboxed. ``path``
        is relative to that root; traversal/symlink escape is rejected.
        """
        root = await core.get_run_artifacts_dir(run_id)
        if root is None:
            raise ValueError(f"unknown run {run_id}")
        if not root.exists():
            raise ValueError(f"no artifacts for run {run_id}")
        try:
            target = resolve_within_sandbox(root, path)
        except SandboxViolation as exc:
            raise ValueError(f"invalid artifact path: {exc}") from exc
        if not target.is_file():
            raise ValueError(f"artifact not found: {path!r}")
        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"artifact too large: {size} bytes > {MAX_FILE_BYTES} "
                f"limit"
            )
        raw = target.read_bytes()
        if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
            raise ValueError("artifact is binary; not readable as text")
        return raw.decode("utf-8", errors="replace")

    return mcp
