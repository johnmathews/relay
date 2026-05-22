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
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from relay_v2.config import Settings, get_settings
from relay_v2.db import (
    init_db,
    make_async_engine,
    make_async_sessionmaker,
)
from relay_v2.db.models import Event, Iter, Project, Prompt, Run
from relay_v2.events import EventStore
from relay_v2.harness import Harness
from relay_v2.harness.pi import PiHarness
from relay_v2.observability import (
    Instrumentation,
    IterSpanContext,
    build_instrumentation,
)
from relay_v2.orchestrator.lifecycle import (
    RunContext,
    compose_join_prompt,
    compose_resume_prompt,
    create_run,
    latest_fanout_iter,
    latest_paused_iter,
    load_run,
    provision_workspace,
    register_project,
    set_run_status,
)
from relay_v2.orchestrator.loop import LoopResult, SessionHandle, run_loop
from relay_v2.orchestrator.preamble import build_preamble, compose_prompt
from relay_v2.sse import Broadcaster

__all__ = ["RelayCore"]

logger = logging.getLogger(__name__)


@dataclass
class _RunState:
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    session_handle: SessionHandle = field(default_factory=SessionHandle)
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    result: LoopResult | None = None
    # ADR-38 (Phase 9f, Task 4): opaque OTel iter context from the parent's
    # dispatching fanout iter.  Set by _dispatch_children before enqueue so
    # _run can pass it to otel.run_span() and parent the child's trace span
    # under the iter that triggered the fanout.  None for top-level (root)
    # runs and for synthesizer re-enqueues (_maybe_resume_parent creates a
    # fresh _RunState without this field — Task 4b wires the synth path).
    parent_iter_ctx: IterSpanContext | None = None


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
        terminal = ("done", "failed", "cancelled")
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
        terminal = ("done", "failed", "cancelled")
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
        from relay_v2.harness.signaling.fanout import FanoutPayload

        parent_depth = await self._fanout_depth(parent_run_id)
        if parent_depth + 1 > self._settings.max_fanout_depth:
            raise ValueError(
                f"fanout depth limit: parent {parent_run_id} is at depth "
                f"{parent_depth}, max_fanout_depth="
                f"{self._settings.max_fanout_depth}"
            )

        payload = FanoutPayload.model_validate(fanout_payload)

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
                self._settings.data_dir,
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
        terminal = ("done", "failed", "cancelled")
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

            run_dir = self._settings.data_dir / "runs" / parent_run_id
            phase_file = run_dir / "phase"
            phase = (
                phase_file.read_text().strip()
                if phase_file.exists() else None
            )
            self._runs[parent_run_id] = _RunState()
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
            "done", "failed", "cancelled"
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
        # ADR-38 (9f Task 4): pass the dispatching iter's OTel context so
        # child runs are parented under the fanout iter in the trace tree.
        # state.parent_iter_ctx is None for root runs (correct — they are
        # trace roots) and None for synthesizer re-enqueues until Task 4b
        # wires _maybe_resume_parent. The cancelled-before-start guard
        # above must remain above this line so cascade-DB-finalised
        # descendants never open a span.
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
        await set_run_status(
            self._sm, ctx.run_id, result.status, ended=True
        )
        await self._store.append(
            ctx.run_id,
            "run_ended",
            {"status": result.status,
             "summary": result.summary or result.reason},
        )

    # ── public API (write path; ADR-07/ADR-15) ─────────────────────────

    async def register_project(self, root_path: Path, name: str) -> int:
        return await register_project(self._sm, root_path, name)

    async def start_run(
        self,
        project_id: int,
        prompt_body: str,
        *,
        max_iters: int | None = None,
        iter_timeout: int | None = None,
        parent_run_id: str | None = None,
    ) -> str:
        async with self._sm() as s:
            project = await s.get(Project, project_id)
            if project is None:
                raise ValueError(f"unknown project_id={project_id}")
            project_root = Path(project.root_path)

        run_id = self._new_run_id()
        wt, branch, run_dir = await provision_workspace(
            project_root, self._settings.data_dir, run_id
        )
        max_i = max_iters or self._settings.max_iters
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
        )
        await self._store.append(
            run_id,
            "run_started",
            {"project_id": project_id, "prompt_body": prompt_body,
             "max_iters": max_i},
        )
        self._runs[run_id] = _RunState()
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
            )
        )
        return run_id

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
            if run.status in ("done", "failed", "cancelled"):
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
            paused = await latest_paused_iter(self._sm, run_id)
            if paused is None or paused.signal_args is None:
                raise ValueError(f"run {run_id} has no saved pause prompt")
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
            args: dict[str, Any] = dict(paused.signal_args)
            body = compose_resume_prompt(
                str(args.get("next_prompt", "")),
                str(args.get("question", "")),
                answer,
            )
            # Projection first, then the append-only event — same order
            # the loop's other transitions use (ADR-10 consumers see a
            # consistent status when the event lands).
            await set_run_status(self._sm, run_id, "running", ended=False)
            await self._store.append(
                run_id, "pause_resolved", {"answer": answer}
            )

            run_dir = self._settings.data_dir / "runs" / run_id
            phase_file = run_dir / "phase"
            phase = (
                phase_file.read_text().strip()
                if phase_file.exists()
                else None
            )
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
                    start_seq=paused.seq,
                    phase=phase,
                    body=body,
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
    ) -> list[Run]:
        """List runs for a project (or all if ``project_id`` is None).

        By default returns only top-level runs (``parent_run_id IS NULL``);
        pass ``include_children=True`` to include child runs dispatched via
        fanout. The dashboard Run lists (spec.md §9.1, 9e) default-hide children
        so the list stays readable when fanout is in use.
        """
        async with self._sm() as s:
            stmt = select(Run).order_by(Run.started_at.desc())
            if project_id is not None:
                stmt = stmt.where(Run.project_id == project_id)
            if not include_children:
                stmt = stmt.where(Run.parent_run_id.is_(None))
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

    async def delete_project(self, project_id: int) -> bool:
        """Unregister a project: delete ONLY the ``projects`` row. Never
        touches files on disk (spec.md §7 DELETE /api/projects/:id "does
        not delete files on disk"). False if id unknown, True if deleted."""
        async with self._sm() as s:
            row = await s.get(Project, project_id)
            if row is None:
                return False
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
        (``settings.data_dir / "runs" / <run_id>``) but never created."""
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
        run_dir = self._settings.data_dir / "runs" / "<preview>"
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
