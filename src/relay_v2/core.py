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
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from relay_v2.config import Settings, get_settings
from relay_v2.db import (
    init_db,
    make_async_engine,
    make_async_sessionmaker,
)
from relay_v2.db.models import Project, Run
from relay_v2.events import EventStore
from relay_v2.harness import Harness
from relay_v2.harness.pi import PiHarness
from relay_v2.orchestrator.lifecycle import (
    RunContext,
    compose_resume_prompt,
    create_run,
    latest_paused_iter,
    load_run,
    provision_workspace,
    register_project,
    set_run_status,
)
from relay_v2.orchestrator.loop import LoopResult, SessionHandle, run_loop

__all__ = ["RelayCore"]


@dataclass
class _RunState:
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    session_handle: SessionHandle = field(default_factory=SessionHandle)
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    result: LoopResult | None = None


class RelayCore:
    """In-process service layer + orchestrator runtime."""

    def __init__(
        self, settings: Settings | None = None, harness: Harness | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._harness: Harness = harness or PiHarness(self._settings)
        self._engine = make_async_engine(self._settings.async_db_url)
        self._sm = make_async_sessionmaker(self._engine)
        self._store = EventStore(self._sm)
        self._queue: asyncio.Queue[RunContext] = asyncio.Queue()
        self._runs: dict[str, _RunState] = {}
        self._supervisor: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        # Serialises the check-and-enqueue in resume_run so a duplicate
        # resume can't spawn two loops for one run (→ UNIQUE(run_id, seq)
        # violation). Single-user MVP (ADR-12) makes this rare, but the
        # guard is cheap and the right pattern before Phase 3 wires HTTP.
        self._enqueue_lock = asyncio.Lock()

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
        self._supervisor = asyncio.create_task(self._supervise())

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

    # ── supervisor ─────────────────────────────────────────────────────

    async def _supervise(self) -> None:
        while True:
            ctx = await self._queue.get()
            task = asyncio.create_task(self._run(ctx))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self._queue.task_done()

    async def _run(self, ctx: RunContext) -> None:
        state = self._runs[ctx.run_id]
        try:
            try:
                result = await run_loop(
                    ctx,
                    harness=self._harness,
                    store=self._store,
                    cancel_event=state.cancel_event,
                    session_handle=state.session_handle,
                )
            except asyncio.CancelledError:
                state.result = LoopResult("cancelled", reason="shutdown")
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
            state.result = result
            await self._apply_result(ctx, result)
        finally:
            # Guarantee waiters wake even if _apply_result raised — a
            # never-set settled would hang wait_for_run forever.
            if state.result is None:
                state.result = LoopResult(
                    "failed", reason="internal_error"
                )
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
            )
        )
        return run_id

    async def cancel_run(self, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
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

    async def list_runs(self, project_id: int | None = None) -> list[Run]:
        async with self._sm() as s:
            stmt = select(Run).order_by(Run.started_at.desc())
            if project_id is not None:
                stmt = stmt.where(Run.project_id == project_id)
            return list(await s.scalars(stmt))

    async def get_run(self, run_id: str) -> Run | None:
        return await load_run(self._sm, run_id)

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
