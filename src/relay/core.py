"""RelayCore — the single shared service layer (ADR-07, ADR-15).

Every write flows through this object: the loop, and (in later phases)
the REST routes and MCP tools all hold the *same* in-process
``RelayCore`` and mutate state only through it. Phase 2 builds the
service + the orchestrator runtime; no route handlers exist yet and are
not anticipated here.

Runtime model (ADR-19): ``RelayCore`` owns an ``asyncio.Queue`` of
run-start requests and a long-lived **supervisor** task that drains it,
launching one tracked child task per run. This is the open-ended-server
equivalent of plan.md's "TaskGroup in lifespan": a literal
``async with asyncio.TaskGroup()`` cannot stay open while continuing to
accept new work, so the supervisor owns a task set it cancels on
``aclose()``. ``start()`` / ``aclose()`` bracket the lifetime and are
driven by FastAPI's lifespan.

Pause/resume (ADR-20): a paused run persists its next-prompt + question
in the pausing iter's ``signal_args``. ``resume_run`` composes the saved
next-prompt with the human's answer, restores the phase from
``$RELAY_RUN_DIR/phase``, and re-enqueues the run to continue at the
next seq — fresh context per iter still holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select

from relay.config import Settings, get_settings
from relay.db import (
    init_db,
    make_async_engine,
    make_async_sessionmaker,
)
from relay.db.models import Event, Iter, Project, Prompt, Run
from relay.events import EventStore
from relay.harness import Harness
from relay.harness.pi import PiHarness
from relay.observability import (
    Instrumentation,
    IterSpanContext,
    build_instrumentation,
)
from relay.orchestrator.lifecycle import (
    RunContext,
    compose_join_prompt,
    compose_resume_prompt,
    create_run,
    latest_fanout_iter,
    latest_paused_iter,
    load_run,
    project_data_dir,
    provision_workspace,
    register_project,
    set_run_status,
)
from relay.orchestrator.loop import LoopResult, SessionHandle, run_loop
from relay.orchestrator.preamble import build_preamble, compose_prompt
from relay.sse import Broadcaster

__all__ = ["PauseReviewError", "RelayCore"]

logger = logging.getLogger(__name__)


class PauseReviewError(Exception):
    """Raised when :meth:`RelayCore.write_artifact`'s preconditions are
    not met (run not paused, no ``review_path``, path mismatch, oversize,
    binary, missing parent dir, unknown run). The ``code`` attribute lets
    the REST adapter map to the right HTTP status without string-matching
    the message (ADR-40)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _normalise_review_path(p: str) -> str:
    """Canonicalise a ``review_path`` for the ``signal_args`` vs
    request-path equality check: strip leading ``./``, collapse ``/./``,
    but do **not** resolve symlinks (that is the sandbox resolver's
    job). Two paths are considered equal iff their normalised forms
    match (ADR-40)."""
    return str(PurePosixPath(p))


@dataclass
class _RunState:
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    session_handle: SessionHandle = field(default_factory=SessionHandle)
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    result: LoopResult | None = None
    # parent_iter_ctx — captured OTel Context of the dispatching iter span
    # when this run was spawned by a fanout (set by _dispatch_children on
    # child rows; set by _maybe_resume_parent on the parent's synthesizer-
    # phase _RunState). None for top-level user-initiated runs, which are
    # trace roots. ADR-38.
    parent_iter_ctx: IterSpanContext | None = None
    # W3 (chat-mode close): close_chat on a running chat re-uses the
    # cancel signalling path but the final status must land as ``closed``
    # rather than ``cancelled``. Flag is set under ``_enqueue_lock``
    # immediately before signalling; ``_apply_result`` consults it when
    # the loop returns ``LoopResult("cancelled", ...)`` and swaps the
    # terminal status + summary. No effect on task-mode runs (which
    # never call close_chat) or any other code path.
    closing: bool = False


class RelayCore:
    """In-process service layer + orchestrator runtime."""

    def __init__(
        self,
        settings: Settings | None = None,
        harness: Harness | None = None,
        otel: Instrumentation | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._harness: Harness = harness or PiHarness(self._settings)
        # OTel mirror (Phase 7, ADR-29). Injected like the harness seam
        # (ADR-24) so tests pass an in-memory exporter; otherwise built
        # from RELAY_OTEL_EXPORT (a strict no-op when 'none').
        self._otel: Instrumentation = (
            otel if otel is not None
            else build_instrumentation(self._settings)
        )
        self._engine = make_async_engine(self._settings.async_db_url)
        self._sm = make_async_sessionmaker(self._engine)
        # SSE fan-out (ADR-23): a post-commit passive observer attached
        # to the single EventStore.append chokepoint. Lives behind
        # RelayCore (ADR-07/15); routes reach it via
        # ``app.state.core.broadcaster`` and never construct their own.
        self.broadcaster = Broadcaster()
        self._store = EventStore(self._sm, self.broadcaster)
        # Read-only view of settings for thin API adapters (e.g. the
        # New Run wizard's defaults endpoint). Routes never mutate it.
        self.settings = self._settings
        self._queue: asyncio.Queue[RunContext] = asyncio.Queue()
        self._runs: dict[str, _RunState] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        # Serialises the check-and-enqueue in resume_run so a duplicate
        # resume can't spawn two loops for one run (→ UNIQUE(run_id, seq)
        # violation). Single-user MVP (ADR-12) makes this rare, but the
        # guard is cheap and the right pattern before Phase 3 wires HTTP.
        self._enqueue_lock = asyncio.Lock()
        # Fanout concurrency cap (ADR-35, 9b). Initialized in start()
        # after the event loop exists; None before then.
        self._fanout_sem: asyncio.Semaphore | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Ensure the schema exists and start the supervisor. Idempotent
        ``create_all`` (ADR-17) — safe even if the app already ran it.

        RelayCore needs only schema *existence*; it owns its own async
        engine (``self._engine``). The sync engine ``init_db`` returns is
        bootstrap-only and must not outlive this method — dispose it
        immediately so its connection pool does not leak (ADR-21)."""
        bootstrap_engine = init_db(self._settings)
        bootstrap_engine.dispose()
        self._fanout_sem = asyncio.Semaphore(
            self._settings.max_fanout_concurrent
        )
        await self._recover_orphans()
        self._supervisor = asyncio.create_task(self._supervise())

    async def _recover_orphans(self) -> None:
        """Finalise any pre-existing in-flight run from a prior process
        (ADR-31, ADR-32, ADR-34).

        Single-user, single-process MVP (ADR-12): if a row is 'running'
        at startup it cannot be owned by any in-process task — the
        previous process owned it and is gone. Such a run can never
        emit another event, so leaving it stuck means the dashboard
        SSE-tails an event stream that will never produce another
        event and the Cancel button is a no-op (no in-memory state
        for ``cancel_run`` to flip). Mark each as 'cancelled' with
        a clear summary and the closing ``run_ended`` so consumers
        see a terminal state. 'paused' rows are NOT swept — they can
        legitimately be resumed across a restart (``resume_run``
        rebuilds the loop from DB state).

        ``awaiting_children`` rows are swept under the S1 convention
        (ADR-34, 9a): cancel the parent AND cascade-cancel its
        descendants. Recovering an in-flight fanout across a restart
        is a deliberate V1 non-goal — once the parent's in-memory
        watcher is gone, the children would have no consumer for
        their ``subagent_return`` events and the parent would never
        receive its synthesizer iter. Tear the whole subtree down.

        Order matters: cascade-from-awaiting-parents FIRST, then sweep
        the remaining ``running`` rows. A child of an awaiting parent
        is itself ``running`` and would otherwise be matched by both
        passes — the cascade gives it the more-specific
        "parent interrupted during fanout" summary; the second pass
        must skip the rows it already finalised."""
        async with self._sm() as s:
            awaiting = list(
                await s.scalars(
                    select(Run).where(Run.status == "awaiting_children")
                )
            )
        for parent in awaiting:
            await self._cascade_cancel_descendants(
                parent.id,
                summary="orphaned: parent interrupted during fanout",
                _visited={parent.id},
            )
            await set_run_status(
                self._sm, parent.id, "cancelled", ended=True
            )
            await self._store.append(
                parent.id,
                "run_ended",
                {"status": "cancelled",
                 "summary": "orphaned: server restart"},
            )

        # Pass 2: stuck `running` rows from a prior process whose
        # owning task is gone (ADR-32). Re-query so a row finalised by
        # the cascade above is no longer in the set.
        async with self._sm() as s:
            running = list(
                await s.scalars(select(Run).where(Run.status == "running"))
            )
        for run in running:
            await set_run_status(
                self._sm, run.id, "cancelled", ended=True
            )
            await self._store.append(
                run.id,
                "run_ended",
                {"status": "cancelled",
                 "summary": "orphaned: server restart"},
            )

    async def _cascade_cancel_descendants(
        self, parent_run_id: str, *, summary: str,
        _visited: set[str] | None = None,
    ) -> None:
        """Cancel every non-terminal run descended from ``parent_run_id``.

        Used by orphan recovery on startup (ADR-34, 9a) and reserved
        for the runtime cancel-cascade in 9d. Depth-first so a child's
        children resolve before the child itself: a parent cannot
        observe a half-finished subtree mid-cascade. Skips children
        already in a terminal status (no duplicate ``run_ended`` event).

        ``_visited`` is an internal cycle guard threaded through the
        recursion. Production code paths cannot create a cycle (a row's
        ``parent_run_id`` is set once at child-run creation in 9b), but
        a malformed DB — including a self-cycle written by a test or by
        a manual SQL edit — must not loop forever. Seed it with the
        starting parent at the top-level call site so the first recursion
        cannot revisit the root.
        """
        terminal = ("done", "failed", "cancelled", "closed")
        visited = _visited if _visited is not None else set()
        async with self._sm() as s:
            children = list(
                await s.scalars(
                    select(Run).where(Run.parent_run_id == parent_run_id)
                )
            )
        for child in children:
            if child.id in visited:
                continue
            if child.status in terminal:
                continue
            visited.add(child.id)
            await self._cascade_cancel_descendants(
                child.id, summary=summary, _visited=visited
            )
            await set_run_status(self._sm, child.id, "cancelled", ended=True)
            await self._store.append(
                child.id,
                "run_ended",
                {"status": "cancelled", "summary": summary},
            )

    async def _cascade_cancel_runtime(
        self, parent_run_id: str, *, summary: str,
        _visited: set[str] | None = None,
    ) -> None:
        """Runtime cancel-cascade: signal in-flight descendants and
        DB-finalise the rest (9d).

        Sibling of :meth:`_cascade_cancel_descendants` (the DB-only
        startup variant, ADR-34) — kept distinct because at runtime we
        must NOT pre-finalise a row whose ``_run`` task is alive (that
        would race the task's own CancelledError finalisation and
        double-emit ``run_ended``). Per-descendant strategy:

        - ``self._runs[id]`` exists and ``not settled.is_set()``: set
          ``cancel_event`` + cancel the harness session. The ``_run``
          task's CancelledError branch owns the DB write.
        - otherwise (no in-memory state, or state already settled):
          write ``set_run_status(cancelled, ended=True)`` + ``run_ended``
          via the same path as :meth:`_cascade_cancel_descendants`.

        Depth-first: a grandchild settles before its parent, so the
        intermediate parent observes a fully-cancelled subtree.
        """
        terminal = ("done", "failed", "cancelled", "closed")
        visited = _visited if _visited is not None else set()
        async with self._sm() as s:
            children = list(
                await s.scalars(
                    select(Run).where(Run.parent_run_id == parent_run_id)
                )
            )
        for child in children:
            if child.id in visited:
                continue
            if child.status in terminal:
                continue
            visited.add(child.id)
            # Recurse first so grandchildren settle before the child.
            await self._cascade_cancel_runtime(
                child.id, summary=summary, _visited=visited
            )
            state = self._runs.get(child.id)
            if state is not None and not state.settled.is_set():
                # In-flight: signal and let _run finalise.
                state.cancel_event.set()
                session = state.session_handle.session
                if session is not None:
                    await session.cancel()
            else:
                # DB-only: queued, lost state, or already settled.
                await set_run_status(
                    self._sm, child.id, "cancelled", ended=True
                )
                await self._store.append(
                    child.id,
                    "run_ended",
                    {"status": "cancelled", "summary": summary},
                )

    async def _fanout_depth(self, run_id: str) -> int:
        """Walk the parent_run_id chain and return the depth (0 = root).

        Bounded by ``max_fanout_depth + 1`` hops to guard against a
        malformed DB cycle.
        """
        depth = 0
        current_id: str | None = run_id
        cap = self._settings.max_fanout_depth + 2
        while current_id is not None and depth <= cap:
            async with self._sm() as s:
                row = await s.get(Run, current_id)
            if row is None:
                break
            current_id = row.parent_run_id
            if current_id is not None:
                depth += 1
        return depth

    async def _dispatch_children(
        self,
        parent_run_id: str,
        parent_worktree_path: Path | None,
        fanout_payload: dict[str, Any],
        iter_id: int | None,
        parent_iter_ctx: IterSpanContext | None = None,
    ) -> None:
        """Create N child runs and enqueue them (spec.md §6, 9b).

        Depth bound (ADR-35): raises ``ValueError`` when
        ``depth(parent) + 1 > max_fanout_depth``.
        """
        from relay.harness.signaling.fanout import FanoutPayload

        parent_depth = await self._fanout_depth(parent_run_id)
        if parent_depth + 1 > self._settings.max_fanout_depth:
            raise ValueError(
                f"fanout depth limit: parent {parent_run_id} is at depth "
                f"{parent_depth}, max_fanout_depth="
                f"{self._settings.max_fanout_depth}"
            )

        payload = FanoutPayload.model_validate(fanout_payload)
        if len(payload.children) > self._settings.max_fanout_width:
            raise ValueError(
                f"fanout width limit: payload lists {len(payload.children)} "
                f"children, max_fanout_width="
                f"{self._settings.max_fanout_width}"
            )

        async with self._sm() as s:
            parent_run = await s.get(Run, parent_run_id)
            if parent_run is None:
                raise ValueError(f"parent run {parent_run_id} not found")
            project_id = parent_run.project_id

        async with self._sm() as s:
            project = await s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id} not found")
            project_root = Path(project.root_path)

        # Two passes (9c): create ALL child rows + dispatch events first,
        # THEN enqueue them. If we interleaved create/enqueue, the
        # supervisor could start the first child and let it finish (the
        # ScriptedHarness path is instantaneous) before we returned to
        # create the second child's row — at which point the
        # ``_maybe_resume_parent`` watcher's "are all children terminal?"
        # check would short-circuit on the partial child set and resume
        # the parent with only one ``subagent_return`` event. Creating
        # all rows up-front guarantees the watcher always sees the full
        # child set.
        contexts: list[RunContext] = []
        for child in payload.children:
            child_run_id = self._new_run_id()
            wt, branch, run_dir = await provision_workspace(
                project_root,
                child_run_id,
                parent_worktree_path=parent_worktree_path,
            )
            await create_run(
                self._sm,
                run_id=child_run_id,
                project_id=project_id,
                prompt_body=child.prompt,
                max_iters=self._settings.max_iters,
                iter_timeout=self._settings.iter_timeout,
                worktree_path=str(wt) if wt else None,
                branch=branch,
                parent_run_id=parent_run_id,
            )
            # subagent_dispatch on the parent stream (spec.md §3.2).
            await self._store.append(
                parent_run_id,
                "subagent_dispatch",
                {
                    "child_run_id": child_run_id,
                    "role": child.role,
                    "prompt": child.prompt,
                },
                iter_id=iter_id,
            )
            # run_started on the child's own stream.
            await self._store.append(
                child_run_id,
                "run_started",
                {
                    "project_id": project_id,
                    "prompt_body": child.prompt,
                    "max_iters": self._settings.max_iters,
                },
            )
            self._runs[child_run_id] = _RunState()
            # ADR-38 (9f Task 4): stash the fanout iter's OTel context on
            # the child's _RunState BEFORE the enqueue pass so _run can
            # read it from state.parent_iter_ctx and parent the child's
            # relay.run span under the dispatching iter in the trace tree.
            # Must happen in this first pass (create-all rows), not the
            # second pass (enqueue), to preserve the 9c invariant: a fast
            # harness must not let child A's _run read an un-set
            # parent_iter_ctx before we get back to set it here.
            self._runs[child_run_id].parent_iter_ctx = parent_iter_ctx
            contexts.append(
                RunContext(
                    run_id=child_run_id,
                    project_root=project_root,
                    worktree_path=wt,
                    run_dir=run_dir,
                    max_iters=self._settings.max_iters,
                    iter_timeout=self._settings.iter_timeout,
                    start_seq=0,
                    phase=None,
                    body=child.prompt,
                    parent_run_id=parent_run_id,
                )
            )
        # Enqueue only after every child row exists, so the watcher
        # cannot observe a partial child set.
        for ctx in contexts:
            await self._queue.put(ctx)

    async def _collect_child_results(
        self, parent_run_id: str
    ) -> list[dict[str, str]]:
        """Gather per-child result dicts for the synthesizer trailer (9c).

        Ordering: children sorted by ``started_at`` asc so the trailer is
        deterministic and matches dispatch order. Role is recovered from
        the parent's ``subagent_dispatch`` events (we don't store role
        on the child run row — single source of truth is the dispatch
        event, ADR-10). Summary is the closing ``run_ended`` event's
        ``summary`` field (empty string if absent).

        Schema of each entry (all ``str``):
        ``id`` / ``role`` / ``status`` / ``summary`` / ``branch`` /
        ``worktree_path``. Empty branch / worktree_path become empty
        strings, never None — the trailer formatter expects strings.
        """
        async with self._sm() as s:
            children = list(
                await s.scalars(
                    select(Run)
                    .where(Run.parent_run_id == parent_run_id)
                    .order_by(Run.started_at.asc())
                )
            )
            # role lookup from subagent_dispatch events on the parent.
            dispatches = list(
                await s.scalars(
                    select(Event).where(
                        Event.run_id == parent_run_id,
                        Event.kind == "subagent_dispatch",
                    )
                )
            )
        role_by_child: dict[str, str] = {}
        for ev in dispatches:
            cid = ev.payload.get("child_run_id")
            role = ev.payload.get("role", "")
            if isinstance(cid, str):
                role_by_child[cid] = role if isinstance(role, str) else ""

        results: list[dict[str, str]] = []
        for child in children:
            async with self._sm() as s:
                ended = await s.scalar(
                    select(Event)
                    .where(
                        Event.run_id == child.id,
                        Event.kind == "run_ended",
                    )
                    .order_by(Event.seq.desc())
                    .limit(1)
                )
            summary = ""
            if ended is not None:
                raw = ended.payload.get("summary", "")
                summary = raw if isinstance(raw, str) else ""
            results.append({
                "id": child.id,
                "role": role_by_child.get(child.id, ""),
                "status": child.status,
                "summary": summary,
                "branch": child.branch or "",
                "worktree_path": child.worktree_path or "",
            })
        return results

    async def _maybe_resume_parent(self, parent_run_id: str) -> None:
        """If ``parent_run_id`` is in ``awaiting_children`` AND all its
        children have reached a terminal status, emit one
        ``subagent_return`` per child + one ``child_runs_resolved``,
        transition the parent to ``running``, and re-enqueue it with a
        synthesizer ``RunContext`` (9c).

        No-op (silent) when:
        - the parent row is unknown,
        - the parent status is not ``awaiting_children`` (already
          resumed, cascade-cancelled by 9d/restart, or never awaiting),
        - any child is still non-terminal.

        The single-user MVP ``_enqueue_lock`` (already serialising
        ``resume_run``'s look-then-decide-then-enqueue) is reused so
        two near-simultaneous child terminals can't both resume the
        parent.
        """
        terminal = ("done", "failed", "cancelled", "closed")
        async with self._enqueue_lock:
            async with self._sm() as s:
                parent = await s.get(Run, parent_run_id)
                if parent is None or parent.status != "awaiting_children":
                    return
                children = list(
                    await s.scalars(
                        select(Run).where(
                            Run.parent_run_id == parent_run_id
                        )
                    )
                )
            if not children:
                return
            if any(c.status not in terminal for c in children):
                return
            # All children terminal: emit returns + resolved, transition,
            # enqueue synthesizer.
            results = await self._collect_child_results(parent_run_id)
            for r in results:
                await self._store.append(
                    parent_run_id,
                    "subagent_return",
                    {
                        "child_run_id": r["id"],
                        "status": r["status"],
                        "summary": r["summary"],
                    },
                )
            await self._store.append(
                parent_run_id,
                "child_runs_resolved",
                {
                    "children_count": len(results),
                    "terminal_statuses": {
                        r["id"]: r["status"] for r in results
                    },
                },
            )

            # Recover join_prompt from the closing fanout iter; refuse
            # to resume if it's missing (defensive — a structurally
            # impossible state given 9b's writes, but we'd rather leave
            # the parent in awaiting_children than enqueue a
            # synthesizer with an empty prompt).
            closing = await latest_fanout_iter(self._sm, parent_run_id)
            if closing is None or closing.signal_args is None:
                logger.error(
                    "fanout-join: parent %s has no closing fanout iter; "
                    "leaving in awaiting_children",
                    parent_run_id,
                )
                return
            payload = closing.signal_args.get("payload") or {}
            if not isinstance(payload, dict):
                logger.error(
                    "fanout-join: parent %s closing iter payload is not "
                    "a dict (got %r); leaving in awaiting_children",
                    parent_run_id, type(payload).__name__,
                )
                return
            join_prompt = payload.get("join_prompt", "")
            if not isinstance(join_prompt, str) or not join_prompt:
                logger.error(
                    "fanout-join: parent %s has empty join_prompt; "
                    "leaving in awaiting_children",
                    parent_run_id,
                )
                return

            body = compose_join_prompt(join_prompt, results)

            # Transition projection first, then enqueue (mirrors
            # resume_run's order so SSE consumers see the status flip
            # before the next iter_started lands).
            await set_run_status(
                self._sm, parent_run_id, "running", ended=False
            )

            # Resolve the project + parent worktree for the new ctx.
            async with self._sm() as s:
                parent_row = await s.get(Run, parent_run_id)
                if parent_row is None:
                    return
                project = await s.get(Project, parent_row.project_id)
            if project is None:
                logger.error(
                    "fanout-join: parent %s project missing; cannot resume",
                    parent_run_id,
                )
                return

            run_dir = (
                project_data_dir(Path(project.root_path)) / "runs"
                / parent_run_id
            )
            phase_file = run_dir / "phase"
            phase = (
                phase_file.read_text().strip()
                if phase_file.exists() else None
            )
            # Preserve the dispatching iter's OTel context from the old
            # _RunState so the synthesizer-phase relay.run span parents under
            # the same iter as the children (one connected fanout-join sub-tree
            # in the trace).  The conditional handles the impossible-but-safe
            # case where result is None — falls back to None (root span),
            # matching the pre-Task-4b default.  No try/except: an unexpected
            # exception here should propagate so the bug is visible.
            #
            # ADR-38: use result.fanout_parent_ctx (the iter where THIS run
            # fanned out), NOT parent_iter_ctx (the iter where THIS run was
            # dispatched FROM). This preserves recursive symmetry: at every
            # level, the synth phase is a sibling of THAT level's children
            # under THAT level's dispatching iter.
            old_state = self._runs.get(parent_run_id)
            preserved_ctx = (
                old_state.result.fanout_parent_ctx
                if old_state is not None and old_state.result is not None
                else None
            )
            self._runs[parent_run_id] = _RunState()
            # ADR-38: synth-phase run-span parents under the same dispatching
            # iter as the children (one connected fanout-join sub-tree).
            self._runs[parent_run_id].parent_iter_ctx = preserved_ctx
            await self._queue.put(
                RunContext(
                    run_id=parent_run_id,
                    project_root=Path(project.root_path),
                    worktree_path=Path(parent_row.worktree_path)
                    if parent_row.worktree_path else None,
                    run_dir=run_dir,
                    max_iters=parent_row.max_iters,
                    iter_timeout=parent_row.iter_timeout,
                    start_seq=closing.seq,  # loop does seq += 1 on entry
                    phase=phase,
                    body=body,
                    parent_run_id=parent_row.parent_run_id,
                )
            )

    async def aclose(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except asyncio.CancelledError:
                pass
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            # A run that failed with an uncaught exception must not stall
            # shutdown — swallow CancelledError *and* any task exception.
            with contextlib.suppress(BaseException):
                await task
        await self._engine.dispose()
        # Flush any buffered spans (no-op for the NOOP instrumentation).
        self._otel.shutdown()

    # ── supervisor ─────────────────────────────────────────────────────

    async def _supervise(self) -> None:
        while True:
            ctx = await self._queue.get()
            if ctx.parent_run_id is not None and self._fanout_sem is not None:
                # Child run: acquire slot before creating the task so at
                # most max_fanout_concurrent children run concurrently.
                # The done-callback releases regardless of outcome.
                await self._fanout_sem.acquire()
                task = asyncio.create_task(self._run(ctx))
                sem = self._fanout_sem

                def _release(t: asyncio.Task[None], s: asyncio.Semaphore = sem) -> None:
                    s.release()

                task.add_done_callback(_release)
            else:
                task = asyncio.create_task(self._run(ctx))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self._queue.task_done()

    async def _run(self, ctx: RunContext) -> None:
        state = self._runs[ctx.run_id]
        # 9d guard: a row pre-flipped to cancelled (cascade DB-only
        # branch) that the supervisor picks up should not enter the
        # loop. _RunState's settled.set() is still required so any
        # caller awaiting wait_for_run() does not hang. We skip the
        # _maybe_resume_parent call in _run's finally on purpose: the
        # cascade flips the parent OUT of awaiting_children before
        # finalising children (ADR-37 ordering invariant), so the
        # watcher would observe a non-awaiting parent and no-op anyway.
        run_row = await load_run(self._sm, ctx.run_id)
        if run_row is not None and run_row.status in (
            "done", "failed", "cancelled", "closed"
        ):
            state.result = LoopResult(
                run_row.status,
                reason="cancelled_before_start",
            )
            state.settled.set()
            return
        # ADR-29: the run span lives in _run's try/finally (NOT
        # start_run, which only enqueues), so a crashed or
        # supervisor-cancelled run still closes its span — the `with`
        # records the exception, marks ERROR, and re-raises, never
        # altering loop control flow.
        # ADR-38: pass the dispatching iter's OTel context so fanout-spawned
        # runs are parented under the dispatching iter in the trace tree.
        # state.parent_iter_ctx is None for top-level runs (correct — they
        # are trace roots), the parent's dispatching iter context for fanout
        # children (set by _dispatch_children), and the run's own dispatching
        # iter context for synth-phase re-enqueues (set by
        # _maybe_resume_parent, preserving recursive symmetry). The
        # cancelled-before-start guard above must remain above this line so
        # cascade-DB-finalised descendants never open a span.
        with self._otel.run_span(
            ctx.run_id, parent_iter_ctx=state.parent_iter_ctx
        ) as run_span:
            try:
                try:
                    result = await run_loop(
                        ctx,
                        harness=self._harness,
                        store=self._store,
                        cancel_event=state.cancel_event,
                        session_handle=state.session_handle,
                        otel_run=run_span,
                    )
                    state.result = result
                    await self._apply_result(ctx, result)
                except asyncio.CancelledError:
                    state.result = LoopResult(
                        "cancelled", reason="shutdown"
                    )
                    # Best-effort finalisation: the DB/engine may be mid
                    # dispose during aclose(), so a failure here must not
                    # mask the cancellation.
                    with contextlib.suppress(Exception):
                        await set_run_status(
                            self._sm, ctx.run_id, "cancelled", ended=True
                        )
                        await self._store.append(
                            ctx.run_id, "run_ended",
                            {"status": "cancelled",
                             "summary": "supervisor shutdown"},
                        )
                    raise
                except Exception as exc:
                    # ADR-31: any other exception from the loop or
                    # _apply_result is an internal error of the run —
                    # record it as `failed`/`run_ended` so the DB and
                    # event stream reflect a terminal state. Without this,
                    # a spawn-time error (bad project root, missing pi
                    # binary) leaves the run permanently `running` and
                    # the dashboard SSE-tails an event stream that will
                    # never produce another event.
                    logger.exception(
                        "run %s failed with internal error", ctx.run_id
                    )
                    state.result = LoopResult(
                        "failed", reason="internal_error", summary=str(exc)
                    )
                    # Best-effort finalisation, same caveat as the
                    # cancellation branch above.
                    with contextlib.suppress(Exception):
                        await set_run_status(
                            self._sm, ctx.run_id, "failed", ended=True
                        )
                        await self._store.append(
                            ctx.run_id, "run_ended",
                            {"status": "failed",
                             "summary": f"internal_error: {exc!s}"},
                        )
            finally:
                # Guarantee waiters wake even if _apply_result raised — a
                # never-set settled would hang wait_for_run forever.
                if state.result is None:
                    state.result = LoopResult(
                        "failed", reason="internal_error"
                    )
                # Fanout-join (9c): when a child run settles, give its
                # parent a chance to resume. Idempotent + lock-guarded
                # in _maybe_resume_parent; a no-op when the parent is
                # not awaiting_children (cascade-cancelled, already
                # resumed by a sibling, or this run isn't a child at
                # all). Best-effort — a watcher failure must not leak
                # back into the run task's shutdown. Runs BEFORE
                # state.settled.set() so a caller awaiting this child's
                # wait_for_run() then immediately awaiting the parent's
                # cannot race the watcher's swap of self._runs[parent].
                if ctx.parent_run_id is not None:
                    with contextlib.suppress(Exception):
                        await self._maybe_resume_parent(ctx.parent_run_id)
                state.settled.set()

    async def _apply_result(
        self, ctx: RunContext, result: LoopResult
    ) -> None:
        """Map the loop outcome to the run's final status + the closing
        run-level event (the loop owns iter-level events only)."""
        if result.status == "paused":
            await set_run_status(
                self._sm, ctx.run_id, "paused", ended=False
            )
            await self._store.append(
                ctx.run_id, "pause_requested",
                {"question": result.question or ""},
            )
            return
        if result.status == "awaiting_children":
            # Status first so SSE consumers see a consistent state when
            # the subagent_dispatch events land.
            await set_run_status(
                self._sm, ctx.run_id, "awaiting_children", ended=False
            )
            # Find the closing iter's id for iter-scoped dispatch events.
            async with self._sm() as s:
                closing = await s.scalar(
                    select(Iter)
                    .where(Iter.run_id == ctx.run_id)
                    .order_by(Iter.seq.desc())
                    .limit(1)
                )
            await self._dispatch_children(
                parent_run_id=ctx.run_id,
                parent_worktree_path=ctx.worktree_path,
                fanout_payload=result.fanout_payload or {},
                iter_id=closing.id if closing else None,
                parent_iter_ctx=result.fanout_parent_ctx,
            )
            return
        # W3 (chat-mode close): if close_chat signalled this loop, the
        # loop returns ``cancelled`` (the cancel-event path is shared with
        # cancel_run). Swap to ``closed`` here so the terminal event the
        # rest of the system observes — DB row, run_ended payload, SSE —
        # matches the operator's intent. Only consulted when the live
        # ``_RunState`` exists and ``closing`` was set.
        final_status = result.status
        final_summary = result.summary or result.reason
        if result.status == "cancelled":
            state = self._runs.get(ctx.run_id)
            if state is not None and state.closing:
                final_status = "closed"
                final_summary = "user closed chat"
        await set_run_status(
            self._sm, ctx.run_id, final_status, ended=True
        )
        await self._store.append(
            ctx.run_id,
            "run_ended",
            {"status": final_status, "summary": final_summary},
        )

    # ── public API (write path; ADR-07/ADR-15) ─────────────────────────

    async def register_project(self, root_path: Path, name: str) -> int:
        return await register_project(self._sm, root_path, name)

    async def get_run_artifacts_dir(self, run_id: str) -> Path | None:
        """The run's on-disk artifacts root
        (``<project_root>/.relay/runs/<run_id>``), or ``None`` if the
        run is unknown. The single resolver routes/MCP tools call to
        avoid reaching into ``settings.data_dir`` — the artifacts dir
        is per-project (spec.md §3.3), not per-server.
        """
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            if run is None:
                return None
            project = await s.get(Project, run.project_id)
            if project is None:
                return None
            return (
                project_data_dir(Path(project.root_path)) / "runs" / run_id
            )

    async def write_artifact(
        self,
        run_id: str,
        rel_path: str,
        content: str,
        *,
        editor: str = "dashboard",
    ) -> dict[str, Any]:
        """Write text content to a sandboxed artifact during a paused
        review (spec §6.2, §7; ADR-40).

        Preconditions (raised as :class:`PauseReviewError` with a ``code``
        the REST adapter maps to an HTTP status):

        - ``unknown_run`` — no row for ``run_id``.
        - ``not_paused`` — ``run.status != "paused"``.
        - ``no_review_path`` — paused iter has no ``review_path`` in
          ``signal_args``.
        - ``path_mismatch`` — normalised ``rel_path`` differs from the
          paused iter's normalised ``review_path``.
        - ``too_large`` — encoded body exceeds the GET-side limit
          (``MAX_FILE_BYTES``).
        - ``binary`` — body contains a NUL byte in its sniff window.
        - ``missing_parent_dir`` — ``rel_path`` lies in a subdirectory
          that does not yet exist on disk (14a does not create
          intermediate dirs).

        Sandbox violations (absolute / ``..`` / NUL in path / symlink
        escape) propagate as :class:`SandboxViolation` from
        :func:`resolve_within_sandbox` for the route to map to 400.

        On success: writes the file atomically (tempfile-in-same-dir then
        ``Path.replace``), appends an ``artifact_edited`` event
        iter-scoped to the paused iter, and returns
        ``{"path", "size", "sha256"}`` for the response.
        """
        # Local import to avoid a circular import at module load:
        # ``relay.api.files`` imports ``relay.api.deps`` which
        # imports ``RelayCore`` from this module.
        from relay.api.files import (
            BINARY_SNIFF_BYTES,
            MAX_FILE_BYTES,
            resolve_within_sandbox,
        )

        async with self._sm() as s:
            run = await s.get(Run, run_id)
            if run is None:
                raise PauseReviewError(
                    "unknown_run", f"unknown run {run_id}"
                )
            if run.status != "paused":
                raise PauseReviewError(
                    "not_paused",
                    f"run {run_id} is not paused "
                    f"(status={run.status!r}); "
                    "writes only allowed during a declared pause review",
                )

        paused = await latest_paused_iter(self._sm, run_id)
        # 14f / ADR-41: paths-as-list is the new shape; the singular key
        # is read as a one-element fallback so iters paused under 14a–14d
        # survive a process restart into the 14f code.
        review_paths: list[str] = []
        if paused is not None and paused.signal_args is not None:
            raw_paths = paused.signal_args.get("review_paths")
            if isinstance(raw_paths, list):
                review_paths = [str(p) for p in raw_paths]
            elif "review_path" in paused.signal_args:
                review_paths = [str(paused.signal_args["review_path"])]
        if paused is None or not review_paths:
            raise PauseReviewError(
                "no_review_path",
                f"run {run_id}'s paused iter has no review_path; "
                "no edit target was declared",
            )
        allowed = {_normalise_review_path(p) for p in review_paths}
        requested = _normalise_review_path(rel_path)
        if requested not in allowed:
            raise PauseReviewError(
                "path_mismatch",
                f"requested path {requested!r} is not among the paused "
                f"iter's review_paths {sorted(allowed)!r}",
            )

        body_bytes = content.encode("utf-8")
        if len(body_bytes) > MAX_FILE_BYTES:
            raise PauseReviewError(
                "too_large",
                f"content too large: {len(body_bytes)} bytes "
                f"> {MAX_FILE_BYTES} limit",
            )
        if "\x00" in content[:BINARY_SNIFF_BYTES]:
            raise PauseReviewError(
                "binary",
                "content is not text (NUL byte in the first "
                f"{BINARY_SNIFF_BYTES} characters)",
            )

        artifacts_root = await self.get_run_artifacts_dir(run_id)
        if artifacts_root is None:
            raise PauseReviewError(
                "unknown_run", f"unknown run {run_id}"
            )
        # The artifacts dir is provisioned at start_run; be defensive in
        # case a test seeded the row but never materialised the dir.
        artifacts_root.mkdir(parents=True, exist_ok=True)
        target = resolve_within_sandbox(artifacts_root, rel_path)

        # 14a does not auto-create intermediate dirs. A review_path like
        # ``discussions/notes.md`` is only accepted if ``discussions/``
        # already exists on disk (the agent should have created it).
        if not target.parent.exists():
            raise PauseReviewError(
                "missing_parent_dir",
                f"parent directory for {rel_path!r} does not exist; "
                "14a does not create intermediate directories",
            )

        if target.exists():
            pre_bytes = target.read_bytes()
            sha256_before: str | None = hashlib.sha256(
                pre_bytes
            ).hexdigest()
            size_before = len(pre_bytes)
        else:
            sha256_before = None
            size_before = 0

        # Atomic write: temp file in the same dir, then ``replace``
        # (POSIX-atomic rename on the same filesystem). A mid-write
        # crash leaves the original file intact.
        tmp = target.parent / f".{target.name}.tmp.{secrets.token_hex(4)}"
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(target)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise

        sha256_after = hashlib.sha256(body_bytes).hexdigest()
        size_after = len(body_bytes)
        normalised = _normalise_review_path(rel_path)

        await self._store.append(
            run_id,
            "artifact_edited",
            {
                "path": normalised,
                "size_before": size_before,
                "size_after": size_after,
                "sha256_before": sha256_before,
                "sha256_after": sha256_after,
                "editor": editor,
            },
            iter_id=paused.id,
        )
        return {
            "path": normalised,
            "size": size_after,
            "sha256": sha256_after,
        }

    async def start_run(
        self,
        project_id: int,
        prompt_body: str,
        *,
        max_iters: int | None = None,
        iter_timeout: int | None = None,
        parent_run_id: str | None = None,
        mode: str = "task",
    ) -> str:
        async with self._sm() as s:
            project = await s.get(Project, project_id)
            if project is None:
                raise ValueError(f"unknown project_id={project_id}")
            project_root = Path(project.root_path)

        run_id = self._new_run_id()
        wt, branch, run_dir = await provision_workspace(
            project_root, run_id
        )
        # Chat-mode default cap is higher (200) than task mode (12) — each
        # user turn = one iter and conversations are open-ended (ADR-NN).
        default_max = (
            self._settings.chat_max_iters
            if mode == "chat"
            else self._settings.max_iters
        )
        max_i = max_iters or default_max
        timeout = iter_timeout or self._settings.iter_timeout
        await create_run(
            self._sm,
            run_id=run_id,
            project_id=project_id,
            prompt_body=prompt_body,
            max_iters=max_i,
            iter_timeout=timeout,
            worktree_path=str(wt) if wt else None,
            branch=branch,
            parent_run_id=parent_run_id,
            mode=mode,
        )
        await self._store.append(
            run_id,
            "run_started",
            {"project_id": project_id, "prompt_body": prompt_body,
             "max_iters": max_i, "mode": mode},
        )
        self._runs[run_id] = _RunState()
        if mode == "chat":
            # ADR-NN: chat mode starts paused with no iter rows. The
            # first ``resume_run(answer)`` becomes iter 1's prompt body.
            # No worktree-spawn, no preamble — the run sits idle until
            # the user sends the first message. ``run_started`` is
            # written above for the dashboard timeline; ``pause_requested``
            # below marks the "waiting for first message" boundary so
            # ChatView renders an empty transcript with a focused input.
            await set_run_status(
                self._sm, run_id, "paused", ended=False
            )
            await self._store.append(
                run_id, "pause_requested", {"question": ""},
            )
            state = self._runs[run_id]
            state.result = LoopResult(
                "paused",
                reason="chat_initial",
                question="",
                next_prompt="",
                pause_id=f"chat-{run_id}-0",
            )
            state.settled.set()
            return run_id
        await self._queue.put(
            RunContext(
                run_id=run_id,
                project_root=project_root,
                worktree_path=wt,
                run_dir=run_dir,
                max_iters=max_i,
                iter_timeout=timeout,
                start_seq=0,
                phase=None,
                body=prompt_body,
                parent_run_id=parent_run_id,
                mode=mode,
            )
        )
        return run_id

    async def start_chat(self, project_id: int) -> str:
        """Start a chat-mode run with an empty initial body (W1).

        Chat runs use pi's native multi-turn model: the run sits paused
        immediately so the user can type the first message, which becomes
        the first iter's prompt body. The loop branch that turns this
        into a useful conversation lands in W2; in W1 isolation a chat
        run created here will be picked up by the existing task-mode
        loop and complete trivially against a scripted/empty harness.
        """
        return await self.start_run(
            project_id, prompt_body="", mode="chat"
        )

    async def cancel_run(self, run_id: str) -> None:
        """Cancel ``run_id``.

        Three branches:

        1. **Awaiting children** (9d): the run has fanned out and has no
           ``_run`` task of its own. Acquire ``_enqueue_lock``, flip
           parent to ``cancelled`` (so the join watcher cannot race a
           resume), then cascade-cancel descendants via
           :meth:`_cascade_cancel_runtime`. Fire-and-forget: in-flight
           descendants finalise themselves via their own
           ``CancelledError`` branch.
        2. **In-flight** (normal case): set ``state.cancel_event`` and
           cancel the harness session. ``_run.finally`` writes the DB.
        3. **No in-memory state + DB row stuck** (orphan, ADR-31 safety
           net): finalise the DB row directly so the user sees a
           visible status flip.
        """
        async with self._enqueue_lock:
            run = await load_run(self._sm, run_id)
            if run is None:
                return
            if run.status == "awaiting_children":
                # Parent first (ordering invariant — see ADR-37 + the
                # watcher race comment in _maybe_resume_parent).
                await set_run_status(
                    self._sm, run_id, "cancelled", ended=True
                )
                await self._store.append(
                    run_id,
                    "run_ended",
                    {"status": "cancelled", "summary": "user cancelled"},
                )
                # We hold ``_enqueue_lock`` across the cascade on
                # purpose: that's what closes the watcher race (ADR-37).
                # The cascade's per-descendant ``session.cancel()`` runs
                # under the lock, but descendant count is bounded by
                # ``max_fanout_concurrent × max_fanout_depth``.
                await self._cascade_cancel_runtime(
                    run_id,
                    summary="parent cancelled by user",
                )
                return
            if run.status in ("done", "failed", "cancelled", "closed"):
                # Already terminal — idempotent no-op.
                return

        # Outside the lock: the existing in-flight signal path. The loop
        # itself does not acquire ``_enqueue_lock``, so holding it here
        # would not deadlock — but ``session.cancel()`` may await pi I/O
        # for an unbounded time, and ``resume_run`` / ``_maybe_resume_parent``
        # share this lock. Releasing before the I/O keeps them responsive.
        state = self._runs.get(run_id)
        if state is None:
            # ADR-31 safety net.
            await set_run_status(
                self._sm, run_id, "cancelled", ended=True
            )
            await self._store.append(
                run_id,
                "run_ended",
                {"status": "cancelled",
                 "summary": "orphaned: process state lost"},
            )
            return
        state.cancel_event.set()
        session = state.session_handle.session
        if session is not None:
            await session.cancel()

    async def close_chat(self, run_id: str) -> None:
        """Close a chat-mode run (W3).

        Chat-only sibling of :meth:`cancel_run`: a chat that the operator
        explicitly ended via the dashboard's Close button (or the
        ``relay__close_chat`` MCP tool) flips to a dedicated ``closed``
        terminal status — distinct from ``cancelled`` (operator gave up
        on an in-flight task) and ``done`` (the agent emitted the
        terminating sentinel) so the dashboard can render a neutral
        "ended" pill and not mix human-ended chats into the task-failure
        list.

        Three branches mirror :meth:`cancel_run`:

        1. **Paused** (the common case for chat: user is between turns):
           no loop is alive. Write ``set_run_status(closed, ended=True)``
           + ``run_ended`` directly under ``_enqueue_lock``.
        2. **Running** (user closed while pi was mid-response): set
           ``state.closing = True`` under the lock, then signal the loop
           (``cancel_event`` + ``session.cancel()``) outside the lock.
           The loop returns ``LoopResult("cancelled", ...)`` and
           :meth:`_apply_result` consults ``state.closing`` to swap the
           terminal status from ``cancelled`` to ``closed``.
        3. **Running but no in-memory state** (ADR-31 orphan safety net):
           write the DB row directly with an orphan summary.

        Raises ``ValueError`` for unknown run or non-chat mode. Already-
        terminal runs (including a prior ``closed``) are idempotent
        no-ops — the REST layer pre-checks and returns 409 for them so
        the operator gets feedback; the MCP layer is permissive.
        """
        async with self._enqueue_lock:
            run = await load_run(self._sm, run_id)
            if run is None:
                raise ValueError(f"unknown run {run_id}")
            if run.mode != "chat":
                raise ValueError(
                    f"run {run_id} is not a chat-mode run "
                    f"(mode={run.mode!r})"
                )
            if run.status in ("done", "failed", "cancelled", "closed"):
                # Already terminal — idempotent no-op (mirrors cancel_run).
                return
            if run.status == "paused":
                # No active loop — write directly.
                await set_run_status(
                    self._sm, run_id, "closed", ended=True
                )
                await self._store.append(
                    run_id,
                    "run_ended",
                    {"status": "closed", "summary": "user closed chat"},
                )
                return
            # Anything other than running here would be a chat-mode run
            # in an unexpected state (chats don't fan out, so
            # ``awaiting_children`` is not reachable for them). Treat as
            # state conflict.
            if run.status != "running":
                raise ValueError(
                    f"run {run_id} is not in a closable state "
                    f"(status={run.status!r})"
                )
            state = self._runs.get(run_id)
            if state is None:
                # Orphan safety net (ADR-31): row says running but no
                # in-process task owns it.
                await set_run_status(
                    self._sm, run_id, "closed", ended=True
                )
                await self._store.append(
                    run_id,
                    "run_ended",
                    {"status": "closed",
                     "summary": "orphaned: process state lost"},
                )
                return
            # Mark the state under the lock so _apply_result observes the
            # flag when the loop returns. The actual signalling happens
            # outside the lock (session.cancel() may await pi I/O).
            state.closing = True

        state.cancel_event.set()
        session = state.session_handle.session
        if session is not None:
            await session.cancel()

    async def reopen_failed_as_paused(self, run_id: str) -> None:
        """Convert a ``failed`` run whose last iter ended without a terminal
        sentinel back into a ``paused`` run so the operator can resume it
        with guidance (WU5 — ADR-53).

        Precondition: the run exists, ``status == "failed"``, and the most
        recent iter's ``exit_reason`` is ``"agent_end_no_signal"`` (the
        only value written to ``iters.exit_reason`` for no-signal closes).

        On success: ``status`` flips to ``"paused"``, ``ended_at`` cleared,
        one ``pause_requested`` event appended with a recovery question.

        Raises ``ValueError("unknown run …")`` if the run does not exist.
        Raises ``ValueError("run … is not failed (status='X')")`` if status
        is not ``failed``.
        Raises ``ValueError("run …'s last iter has exit_reason 'X'; only "
        "no-signal failures can be reopened")`` if the last iter is not a
        no-signal failure.
        """
        async with self._sm() as s:
            run = await s.get(Run, run_id)
            if run is None:
                raise ValueError(f"unknown run {run_id}")
            if run.status != "failed":
                raise ValueError(
                    f"run {run_id} is not failed (status={run.status!r})"
                )
            last_iter = await s.scalar(
                select(Iter)
                .where(Iter.run_id == run_id)
                .order_by(Iter.seq.desc())
                .limit(1)
            )
            eligible_reasons = {
                "agent_end_no_signal",
                "agent_end_no_signal_autopause",
            }
            if (
                last_iter is None
                or last_iter.exit_reason not in eligible_reasons
            ):
                actual = (
                    last_iter.exit_reason
                    if last_iter is not None
                    else "(no iter)"
                )
                raise ValueError(
                    f"run {run_id}'s last iter has exit_reason {actual!r}; "
                    f"only no-signal failures can be reopened"
                )
            # Flip status to paused and explicitly clear ended_at in one
            # transaction. set_run_status(..., ended=False) only skips
            # setting ended_at — it does not clear an existing value — so
            # we write both columns here directly.
            run.status = "paused"
            run.ended_at = None
            await s.commit()
        await self._store.append(
            run_id,
            "pause_requested",
            {
                "question": (
                    "Agent ended without a terminal sentinel; relay "
                    "auto-paused on reopen. Provide guidance to resume, "
                    "or close the run."
                ),
            },
        )

    async def resume_run(self, run_id: str, answer: str) -> None:
        async with self._enqueue_lock:
            run = await load_run(self._sm, run_id)
            if run is None or run.status != "paused":
                raise ValueError(f"run {run_id} is not paused")
            # Belt-and-braces with the DB guard above: an in-memory
            # state that exists but is not settled means a loop is still
            # live for this run — refuse to enqueue a second one.
            live = self._runs.get(run_id)
            if live is not None and not live.settled.is_set():
                raise ValueError(f"run {run_id} is already running")
            # Resolve the project before any side effect: a deleted
            # project must fail loudly, not silently run pi in the
            # process CWD (which corrupts an unrelated directory).
            async with self._sm() as s:
                project = await s.get(Project, run.project_id)
            if project is None:
                raise ValueError(
                    f"run {run_id} project {run.project_id} no longer exists"
                )
            project_root = Path(project.root_path)

            if run.mode == "chat":
                # ADR-NN: chat mode resume — the user's message is the
                # next iter's body verbatim (no preamble, no engteam-style
                # answer composition). For the FIRST message, no prior
                # iter exists and ``resume_session_id`` is None; for
                # subsequent messages, thread the most-recent iter's
                # pi_session_id so pi rehydrates the conversation via
                # its own session storage. Chat mode also has no
                # ``review_paths`` artifact accounting (14e), so
                # ``paused_predecessor_iter_id`` stays None.
                async with self._sm() as s:
                    prior = await s.scalar(
                        select(Iter)
                        .where(Iter.run_id == run_id)
                        .order_by(Iter.seq.desc())
                        .limit(1)
                    )
                body = answer
                resume_session_id = prior.pi_session_id if prior else None
                start_seq = prior.seq if prior else 0
                paused_predecessor_iter_id: int | None = None
                phase: str | None = None
            else:
                paused = await latest_paused_iter(self._sm, run_id)
                if paused is None or paused.signal_args is None:
                    raise ValueError(
                        f"run {run_id} has no saved pause prompt"
                    )
                args: dict[str, Any] = dict(paused.signal_args)
                body = compose_resume_prompt(
                    str(args.get("next_prompt", "")),
                    str(args.get("question", "")),
                    answer,
                )
                resume_session_id = None
                start_seq = paused.seq
                paused_predecessor_iter_id = paused.id
                run_dir_for_phase = (
                    project_data_dir(project_root) / "runs" / run_id
                )
                phase_file = run_dir_for_phase / "phase"
                phase = (
                    phase_file.read_text().strip()
                    if phase_file.exists()
                    else None
                )

            # Projection first, then the append-only event — same order
            # the loop's other transitions use (ADR-10 consumers see a
            # consistent status when the event lands).
            await set_run_status(self._sm, run_id, "running", ended=False)
            await self._store.append(
                run_id, "pause_resolved", {"answer": answer}
            )

            run_dir = project_data_dir(project_root) / "runs" / run_id
            self._runs[run_id] = _RunState()
            await self._queue.put(
                RunContext(
                    run_id=run_id,
                    project_root=project_root,
                    worktree_path=Path(run.worktree_path)
                    if run.worktree_path
                    else None,
                    run_dir=run_dir,
                    max_iters=run.max_iters,
                    iter_timeout=run.iter_timeout,
                    start_seq=start_seq,
                    phase=phase,
                    body=body,
                    # 14e: task-mode resume threads the paused iter's
                    # id so the resumed loop's first iter carries
                    # ``relay.pause.artifacts_edited_count`` on its OTel
                    # iter span. Chat mode leaves this None — there is
                    # no review surface, so the count is structurally
                    # zero and the attribute is omitted.
                    paused_predecessor_iter_id=paused_predecessor_iter_id,
                    mode=run.mode,
                    resume_session_id=resume_session_id,
                )
            )

    async def store_event(
        self,
        run_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        iter_id: int | None = None,
    ) -> int:
        return await self._store.append(
            run_id, kind, payload, iter_id=iter_id
        )

    async def list_runs(
        self,
        project_id: int | None = None,
        *,
        include_children: bool = False,
        mode: str | None = None,
    ) -> list[Run]:
        """List runs for a project (or all if ``project_id`` is None).

        By default returns only top-level runs (``parent_run_id IS NULL``);
        pass ``include_children=True`` to include child runs dispatched via
        fanout. The dashboard Run lists (spec.md §9.1, 9e) default-hide children
        so the list stays readable when fanout is in use.

        ``mode`` filter (W1): ``"task"`` / ``"chat"`` / ``None`` (no filter).
        Chats and tasks share the runs table but the dashboard renders them
        as separate surfaces (ADR-NN).
        """
        async with self._sm() as s:
            stmt = select(Run).order_by(Run.started_at.desc())
            if project_id is not None:
                stmt = stmt.where(Run.project_id == project_id)
            if not include_children:
                stmt = stmt.where(Run.parent_run_id.is_(None))
            if mode is not None:
                stmt = stmt.where(Run.mode == mode)
            return list(await s.scalars(stmt))

    async def list_children(self, parent_run_id: str) -> list[Run]:
        """Direct children of ``parent_run_id``, ordered by started_at asc.

        Returns ``[]`` for a parent that never fanned out. Does NOT walk
        grandchildren — the dashboard pane (spec.md §9.1, 9e) renders one row
        per direct child only. A nested-tree view is a future enhancement.
        """
        async with self._sm() as s:
            stmt = (
                select(Run)
                .where(Run.parent_run_id == parent_run_id)
                .order_by(Run.started_at.asc())
            )
            return list(await s.scalars(stmt))

    async def get_run(self, run_id: str) -> Run | None:
        return await load_run(self._sm, run_id)

    # ── projects (read + unregister; ADR-07/ADR-15) ────────────────────

    async def list_projects(self) -> list[Project]:
        async with self._sm() as s:
            return list(
                await s.scalars(select(Project).order_by(Project.id.asc()))
            )

    async def get_project(self, project_id: int) -> Project | None:
        async with self._sm() as s:
            return await s.get(Project, project_id)

    async def delete_run(self, run_id: str) -> bool:
        """Delete a run and all of its events/iters/descendants.

        DB-only — never touches files on disk (worktree, run_dir);
        mirrors :meth:`delete_project`. The dashboard surfaces this as
        "clear history"; reclaiming on-disk space is a separate manual
        ``rm -rf .relay/runs/<id>`` step.

        Returns ``False`` if ``run_id`` is unknown, ``True`` after a
        successful delete. Raises ``ValueError`` if the run is currently
        active (``running`` or ``awaiting_children``) — those have an
        in-memory task or join-watcher; the caller must
        :meth:`cancel_run` first so the row settles to a terminal status.

        Cascade-deletes child runs (Shape B fanout —
        ``parent_run_id == run_id``) depth-first before removing this
        run's rows. The schema has no ``ON DELETE CASCADE`` (ADR-17
        hand-rolled), so events + iters are removed explicitly.
        """
        async with self._sm() as s:
            row = await s.get(Run, run_id)
            if row is None:
                return False
            if row.status in ("running", "awaiting_children"):
                raise ValueError(
                    f"run {run_id} is {row.status}; "
                    "cancel it before delete"
                )
        children = await self.list_children(run_id)
        for child in children:
            await self.delete_run(child.id)
        async with self._sm() as s:
            await s.execute(sql_delete(Event).where(Event.run_id == run_id))
            await s.execute(sql_delete(Iter).where(Iter.run_id == run_id))
            row = await s.get(Run, run_id)
            if row is not None:
                await s.delete(row)
            await s.commit()
        # Drop the settled _RunState so it can be GC'd. Active rows were
        # refused above; entries here belong only to terminal runs.
        self._runs.pop(run_id, None)
        return True

    async def delete_project(self, project_id: int) -> bool:
        """Unregister a project and cascade-delete its runs + prompts.

        DB-only — never touches files on disk (worktrees, artifacts,
        ``.relay/runs/``). Reclaiming on-disk space is a separate manual
        ``rm -rf`` step.

        Cascade:
          1. Every :class:`Run` with ``project_id == project_id`` — via
             :meth:`delete_run` so each run's events, iters, and
             descendants are removed through the audited path.
          2. Every project-scoped :class:`Prompt`
             (``Prompt.project_id == project_id``). The FK is nullable;
             project-global prompts (``project_id is None``) are
             unaffected.
          3. The :class:`Project` row itself.

        Returns ``False`` if ``project_id`` is unknown, ``True`` after
        a successful cascade. Raises ``ValueError`` if any run is
        currently active (``running``/``awaiting_children``) — the
        caller must :meth:`cancel_run` first. The REST adapter maps
        that to ``409``.
        """
        async with self._sm() as s:
            row = await s.get(Project, project_id)
            if row is None:
                return False
            run_rows = list(
                await s.scalars(
                    select(Run).where(Run.project_id == project_id)
                )
            )
            for r in run_rows:
                if r.status in ("running", "awaiting_children"):
                    raise ValueError(
                        f"project {project_id} has active run {r.id} "
                        f"({r.status}); cancel it before delete"
                    )
        for r in run_rows:
            await self.delete_run(r.id)
        async with self._sm() as s:
            await s.execute(
                sql_delete(Prompt).where(Prompt.project_id == project_id)
            )
            row = await s.get(Project, project_id)
            if row is not None:
                await s.delete(row)
            await s.commit()
        return True

    # ── prompts (versioned; spec.md §3.1 / §7) ─────────────────────────

    async def create_prompt(
        self, project_id: int | None, name: str, body: str
    ) -> Prompt:
        """Insert version 1 of a new prompt. ``ValueError`` if a prompt
        with that (project_id, name) already exists (create = v1 only;
        :meth:`update_prompt` bumps the version) or if ``project_id`` is
        given but unknown."""
        async with self._sm() as s:
            if project_id is not None:
                project = await s.get(Project, project_id)
                if project is None:
                    raise ValueError(f"unknown project_id={project_id}")
            existing = await s.scalar(
                select(Prompt).where(
                    Prompt.project_id == project_id, Prompt.name == name
                )
            )
            if existing is not None:
                raise ValueError(
                    f"prompt name={name!r} already exists "
                    f"for project_id={project_id}"
                )
            row = Prompt(
                project_id=project_id, name=name, version=1, body=body
            )
            s.add(row)
            await s.commit()
            return row

    async def list_prompts(
        self, project_id: int | None = None
    ) -> list[Prompt]:
        """The latest version of each distinct (project_id, name). When
        ``project_id`` is given, filter to that project."""
        async with self._sm() as s:
            latest = (
                select(
                    Prompt.project_id,
                    Prompt.name,
                    func.max(Prompt.version).label("v"),
                )
                .group_by(Prompt.project_id, Prompt.name)
                .subquery()
            )
            stmt = (
                select(Prompt)
                .join(
                    latest,
                    (Prompt.name == latest.c.name)
                    & (Prompt.version == latest.c.v)
                    & (
                        Prompt.project_id.is_not_distinct_from(
                            latest.c.project_id
                        )
                    ),
                )
                .order_by(Prompt.id.asc())
            )
            if project_id is not None:
                stmt = stmt.where(Prompt.project_id == project_id)
            return list(await s.scalars(stmt))

    async def get_prompt(self, prompt_id: int) -> Prompt | None:
        """A specific prompt row (a specific version)."""
        async with self._sm() as s:
            return await s.get(Prompt, prompt_id)

    async def update_prompt(self, prompt_id: int, body: str) -> Prompt:
        """Snapshot update: leave the existing row intact and INSERT a new
        row with the same project_id+name and ``version = max(version
        for that project_id+name) + 1`` (spec.md §7). ``ValueError`` if
        ``prompt_id`` is unknown."""
        async with self._sm() as s:
            current = await s.get(Prompt, prompt_id)
            if current is None:
                raise ValueError(f"unknown prompt_id={prompt_id}")
            max_version = await s.scalar(
                select(func.max(Prompt.version)).where(
                    Prompt.project_id.is_not_distinct_from(
                        current.project_id
                    ),
                    Prompt.name == current.name,
                )
            )
            row = Prompt(
                project_id=current.project_id,
                name=current.name,
                version=int(max_version or 0) + 1,
                body=body,
            )
            s.add(row)
            await s.commit()
            return row

    async def delete_prompt(self, prompt_id: int) -> bool:
        """Delete ALL versions of the (project_id, name) the given id
        belongs to (spec.md §7 "delete a prompt (and all versions)").
        False if ``prompt_id`` is unknown."""
        async with self._sm() as s:
            current = await s.get(Prompt, prompt_id)
            if current is None:
                return False
            rows = list(
                await s.scalars(
                    select(Prompt).where(
                        Prompt.project_id.is_not_distinct_from(
                            current.project_id
                        ),
                        Prompt.name == current.name,
                    )
                )
            )
            for row in rows:
                await s.delete(row)
            await s.commit()
            return True

    async def list_prompt_versions(self, prompt_id: int) -> list[Prompt]:
        """All versions for the (project_id, name) of ``prompt_id``,
        ordered by version asc. Empty list if ``prompt_id`` is unknown."""
        async with self._sm() as s:
            current = await s.get(Prompt, prompt_id)
            if current is None:
                return []
            return list(
                await s.scalars(
                    select(Prompt)
                    .where(
                        Prompt.project_id.is_not_distinct_from(
                            current.project_id
                        ),
                        Prompt.name == current.name,
                    )
                    .order_by(Prompt.version.asc())
                )
            )

    # ── events / iters reads (replay + run detail; ADR-10) ─────────────

    async def list_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Event]:
        """Delegates to :meth:`EventStore.list_events` so the EventStore
        stays the sole owner of the event log (ADR-10, read-only)."""
        return await self._store.list_events(
            run_id, after_seq=after_seq, limit=limit, offset=offset
        )

    async def list_iters(self, run_id: str) -> list[Iter]:
        async with self._sm() as s:
            return list(
                await s.scalars(
                    select(Iter)
                    .where(Iter.run_id == run_id)
                    .order_by(Iter.seq.asc())
                )
            )

    # ── preview (PURE — no side effects: no row/dir/event/DB write) ─────

    async def preview_run(
        self,
        project_id: int,
        *,
        prompt_body: str | None = None,
        prompt_id: int | None = None,
        phase: str | None = None,
    ) -> dict[str, str]:
        """Render the prompt that ``start_run`` *would* send, with zero
        side effects: no ``runs`` row, no ``runs/<id>`` dir, no event, no
        DB write. Exactly one of ``prompt_body`` / ``prompt_id`` must be
        given. The run_dir is a literal ``"<preview>"`` placeholder built
        the same way ``start_run`` derives ``RELAY_RUN_DIR``
        (``<project_root>/.relay/runs/<run_id>``) but never created."""
        if (prompt_body is None) == (prompt_id is None):
            raise ValueError(
                "exactly one of prompt_body / prompt_id must be provided"
            )
        async with self._sm() as s:
            project = await s.get(Project, project_id)
            if project is None:
                raise ValueError(f"unknown project_id={project_id}")
        if prompt_id is not None:
            prompt = await self.get_prompt(prompt_id)
            if prompt is None:
                raise ValueError(f"unknown prompt_id={prompt_id}")
            body = prompt.body
        else:
            assert prompt_body is not None
            body = prompt_body
        run_dir = (
            project_data_dir(Path(project.root_path)) / "runs" / "<preview>"
        )
        return {
            "preamble": build_preamble(run_dir, phase),
            "body": body,
            "prompt": compose_prompt(run_dir, phase, body),
            "run_dir": str(run_dir),
        }

    # ── test/automation helper ─────────────────────────────────────────

    async def wait_for_run(self, run_id: str) -> LoopResult:
        """Block until the run reaches a terminal or paused state and
        return the loop result. Not a route — a deterministic await point
        for tests and scripted automation."""
        state = self._runs[run_id]
        await state.settled.wait()
        assert state.result is not None
        return state.result

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _new_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{ts}-{secrets.token_hex(2)}"
