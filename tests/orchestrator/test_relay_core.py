"""Unit tests for RelayCore service-layer methods (Phase 9e/9f).

Covers:
- list_children: empty for a run with no fanout; direct children only
  (not grandchildren); ordered by started_at asc.
- parent_iter_ctx threading (Phase 9f, Task 4): _dispatch_children
  stashes the ctx on each child's _RunState; _run passes it to
  otel.run_span(); non-fanout runs pass None.

Uses bare ``async def test_*`` with pytest-asyncio auto mode (ADR-24).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session as SyncSession

from relay.config import Settings
from relay.core import RelayCore
from relay.db import init_db
from relay.db.models import Run
from relay.observability import IterSpan, IterSpanContext, RunSpan
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"

FANOUT_TWO = (
    "Dispatching two children.\n\n"
    "[[engteam:fanout-start]]\n"
    "{"
    '"children": ['
    '{"role": "worker-a", "prompt": "Do task A."},'
    '{"role": "worker-b", "prompt": "Do task B."}'
    "],"
    '"join_prompt": "Synthesize the results."'
    "}\n"
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)


# ── stub Instrumentation ────────────────────────────────────────────────


_SENTINEL_CTX = object()  # A non-None stand-in for a real OTel context.


class _StubIterSpan:
    """Minimal IterSpan whose context is a non-None sentinel.

    The loop captures iter_span.context as fanout_parent_ctx in the
    fanout branch (Task 3/loop.py).  Using NOOP_ITER_SPAN.context (=None)
    would make fanout_parent_ctx=None and the test assertion would vacuously
    pass even without the plumbing.  A sentinel makes the non-None assertion
    load-bearing.
    """

    context: IterSpanContext = _SENTINEL_CTX

    def record_tool_call(self, **_: object) -> None:
        pass

    def set_usage(self, messages: object) -> None:
        pass

    def set_exit(self, exit_reason: str) -> None:
        pass


class _RecordingRunSpan:
    """No-op RunSpan that yields a stub iter span with a non-None context."""

    @contextmanager
    def iter_span(
        self,
        *,
        seq: int,
        phase: str | None,
        pause_artifacts_edited_count: int | None = None,
    ) -> Iterator[IterSpan]:
        yield _StubIterSpan()


class RecordingInstrumentation:
    """Stub Instrumentation that captures every run_span call.

    ``calls`` is a list of (run_id, parent_iter_ctx) tuples in the order
    run_span was entered.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, IterSpanContext]] = []

    @contextmanager
    def run_span(
        self, run_id: str, *, parent_iter_ctx: IterSpanContext = None
    ) -> Iterator[RunSpan]:
        self.calls.append((run_id, parent_iter_ctx))
        yield _RecordingRunSpan()

    def shutdown(self) -> None:
        pass


# ── helpers ────────────────────────────────────────────────────────────


async def _make_core(
    tmp_path: Path,
) -> tuple[RelayCore, Settings]:
    """Return a started RelayCore + its Settings for a throw-away data dir."""
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()
    core = RelayCore(
        settings,
        harness=ScriptedHarness([TextScript(DONE_BLOCK)]),
    )
    await core.start()
    return core, settings


async def _make_project(core: RelayCore, project_root: Path) -> int:
    """Register a project and return its id."""
    project_root.mkdir(parents=True, exist_ok=True)
    return await core.register_project(project_root, "test-project")


async def _make_child_run(
    core: RelayCore,
    project_id: int,
    parent_run_id: str,
    prompt_body: str,
) -> str:
    """Insert a child run row directly (no fanout sentinel)."""
    from relay.orchestrator.lifecycle import create_run

    child_id = core._new_run_id()
    await create_run(
        core._sm,
        run_id=child_id,
        project_id=project_id,
        prompt_body=prompt_body,
        max_iters=1,
        iter_timeout=60,
        worktree_path=None,
        branch=None,
        parent_run_id=parent_run_id,
    )
    return child_id


# ── list_children ──────────────────────────────────────────────────────


async def test_list_children_empty_for_run_without_fanout(
    tmp_path: Path,
) -> None:
    """A run with no fanout has no children — empty list, not None."""
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        run_id = await core.start_run(project_id, "hello", max_iters=1)
        children = await core.list_children(run_id)
        assert children == []
    finally:
        await core.aclose()


async def test_list_children_returns_direct_children_only(
    tmp_path: Path,
) -> None:
    """list_children returns rows where parent_run_id == argument.

    Ordered by started_at asc. Recursive (grandchildren) are out of scope for
    9e — the pane renders a flat list per direct child.
    """
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        parent_id = await core.start_run(project_id, "parent", max_iters=1)
        # Directly insert two children + one grandchild via the DB layer (no
        # fanout sentinel needed — we're testing list_children, not dispatch).
        #
        # Insertion order is REVERSED from the expected ASC timestamp order so
        # that the ORDER BY actually does work: without it, SQLite returns rows
        # in insertion order [child_b, child_a]; with it, the backdated
        # started_at on child_a forces [child_a, child_b].  Both orderings
        # agree only when ORDER BY is present.
        child_b = await _make_child_run(core, project_id, parent_id, "child-b")
        child_a = await _make_child_run(core, project_id, parent_id, "child-a")
        _grandchild = await _make_child_run(core, project_id, child_a, "grandchild")

        # SQLite's current_timestamp has 1-second granularity — back-to-back
        # inserts collide.  Backdate child_a so its started_at is strictly
        # earlier than child_b's, making the desired ORDER BY effect visible.
        from sqlalchemy import update

        from relay.db.models import Run

        async with core._sm() as s:
            await s.execute(
                update(Run)
                .where(Run.id == child_a)
                .values(
                    started_at=datetime.now(UTC) - timedelta(seconds=5)
                )
            )
            await s.commit()

        direct = await core.list_children(parent_id)
        # Set equality covers presence; the list equality below also pins order.
        assert {r.id for r in direct} == {child_a, child_b}
        # child_a has the earlier started_at, so ORDER BY started_at ASC must
        # return it first — child_b was inserted first (reversed order) so
        # without ORDER BY the result would be [child_b, child_a], failing here.
        assert [r.id for r in direct] == [child_a, child_b]
    finally:
        await core.aclose()


# ── list_runs ──────────────────────────────────────────────────────────


async def test_list_runs_excludes_children_by_default(
    tmp_path: Path,
) -> None:
    """list_runs() default behaviour: top-level rows only (parent_run_id IS NULL)."""
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        parent_id = await core.start_run(project_id, "parent", max_iters=1)
        _child_id = await _make_child_run(core, project_id, parent_id, "child")

        rows = await core.list_runs(project_id)
        assert {r.id for r in rows} == {parent_id}
    finally:
        await core.aclose()


async def test_list_runs_includes_children_when_requested(
    tmp_path: Path,
) -> None:
    """list_runs(include_children=True) returns the full set."""
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        parent_id = await core.start_run(project_id, "parent", max_iters=1)
        child_id = await _make_child_run(core, project_id, parent_id, "child")

        rows = await core.list_runs(project_id, include_children=True)
        assert {r.id for r in rows} == {parent_id, child_id}
    finally:
        await core.aclose()


# ── W1: chat-mode column + start_chat + list_runs mode filter ──────────


async def test_start_run_defaults_to_task_mode(tmp_path: Path) -> None:
    """Existing start_run callers (no mode kwarg) yield mode='task'.

    Regression guard: a chat-mode default would silently break every
    task-mode test in the suite. The default must stay 'task'.
    """
    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        run_id = await core.start_run(project_id, "hello", max_iters=1)
        run = await core.get_run(run_id)
        assert run is not None
        assert run.mode == "task"
    finally:
        await core.aclose()


async def test_start_chat_creates_chat_mode_run(tmp_path: Path) -> None:
    """start_chat() yields a chat-mode run with empty prompt_body and
    the chat_max_iters cap, not the task max_iters cap. Post-W2 the
    run lands in ``paused`` immediately with no iter rows; the first
    ``resume_run`` becomes iter 1's prompt body.
    """
    settings = Settings(data_dir=tmp_path / ".relay", chat_max_iters=1)
    init_db(settings).dispose()
    core = RelayCore(
        settings,
        harness=ScriptedHarness([TextScript(DONE_BLOCK)]),
    )
    await core.start()
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        chat_id = await core.start_chat(project_id)
        run = await core.get_run(chat_id)
        assert run is not None
        assert run.mode == "chat"
        assert run.prompt_body == ""
        assert run.max_iters == 1  # chat_max_iters override
        result = await core.wait_for_run(chat_id)
        assert result.status == "paused"
    finally:
        await core.aclose()


async def test_list_runs_filters_by_mode(tmp_path: Path) -> None:
    """list_runs(mode=...) returns only rows matching that mode.

    Seeds rows via create_run directly to avoid the (W2-pending) loop
    branch running the chat row's empty body.
    """
    from relay.orchestrator.lifecycle import create_run

    core, _settings = await _make_core(tmp_path)
    try:
        project_id = await _make_project(core, tmp_path / "proj")
        task_id = core._new_run_id()
        chat_id = core._new_run_id()
        await create_run(
            core._sm,
            run_id=task_id,
            project_id=project_id,
            prompt_body="task body",
            max_iters=1,
            iter_timeout=60,
            worktree_path=None,
            branch=None,
            mode="task",
        )
        await create_run(
            core._sm,
            run_id=chat_id,
            project_id=project_id,
            prompt_body="",
            max_iters=1,
            iter_timeout=60,
            worktree_path=None,
            branch=None,
            mode="chat",
        )
        all_rows = await core.list_runs(project_id)
        assert {r.id for r in all_rows} == {task_id, chat_id}

        only_tasks = await core.list_runs(project_id, mode="task")
        assert {r.id for r in only_tasks} == {task_id}

        only_chats = await core.list_runs(project_id, mode="chat")
        assert {r.id for r in only_chats} == {chat_id}
    finally:
        await core.aclose()


# ── Phase 9f Task 4: parent_iter_ctx threading ─────────────────────────


async def test_dispatch_children_stashes_parent_iter_ctx_on_child_state(
    tmp_path: Path,
) -> None:
    """_dispatch_children stashes the fanout_parent_ctx on each child's
    _RunState.parent_iter_ctx BEFORE enqueue.  _run then passes it to
    otel.run_span() as parent_iter_ctx.

    Drive a parent that fans out to 2 children; the recording stub captures
    every run_span call.  Assert:
    - The parent's run_span was opened with parent_iter_ctx=None (it is a
      root run, not a child).
    - Both children's run_span calls carry a non-None parent_iter_ctx.
    - Both children carry the *same* context object (same fanout iter).
    """
    settings = Settings(data_dir=tmp_path / ".relay")
    otel = RecordingInstrumentation()
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE_BLOCK), TextScript(DONE_BLOCK),
         TextScript(DONE_BLOCK)]
    )
    core = RelayCore(settings, harness=harness, otel=otel)
    await core.start()
    try:
        proj_root = tmp_path / "proj"
        proj_root.mkdir(parents=True, exist_ok=True)
        pid = await core.register_project(proj_root, "p")
        parent_id = await core.start_run(pid, "Investigate.")
        # Wait for the parent to settle at awaiting_children.
        first = await core.wait_for_run(parent_id)
        assert first.status == "awaiting_children", first.status
        # Children rows exist in DB now (created before enqueue — 9c invariant).
        # Fetch their IDs from DB so we can await them.
        engine = create_engine(settings.db_url)
        try:
            with SyncSession(engine) as s:
                child_rows = list(
                    s.scalars(
                        sa_select(Run).where(Run.parent_run_id == parent_id)
                    )
                )
        finally:
            engine.dispose()
        child_ids = [c.id for c in child_rows]
        assert len(child_ids) == 2
        for cid in child_ids:
            await core.wait_for_run(cid)
        # Wait for synthesizer (parent reaches done on its second _RunState).
        second = await core.wait_for_run(parent_id)
        assert second.status == "done", second.status
    finally:
        await core.aclose()

    # Parent was opened with no parent_iter_ctx (it is a root run).
    parent_calls = [(rid, ctx) for rid, ctx in otel.calls if rid == parent_id]
    # The first (fanout) parent run_span call must have parent_iter_ctx=None.
    assert parent_calls[0][1] is None

    # Both children must have been opened with a non-None parent_iter_ctx.
    child_ctxs = [ctx for rid, ctx in otel.calls if rid in child_ids]
    assert len(child_ctxs) == 2
    for ctx in child_ctxs:
        assert ctx is not None, "child run_span must have a parent_iter_ctx"
    # Both children share the same ctx object (dispatched from the same iter).
    assert child_ctxs[0] is child_ctxs[1]

    # Task 4b: the synthesizer-phase parent run_span call must also carry
    # the dispatching iter's ctx, so it parents under the same iter as
    # the children (one connected fanout-join sub-tree per ADR-38).
    assert len(parent_calls) == 2, (
        f"expected exactly 2 parent run_span calls (pre-fanout + synth), "
        f"got {len(parent_calls)}"
    )
    synth_ctx = parent_calls[1][1]
    assert synth_ctx is not None, (
        "synthesizer-phase run_span must carry parent_iter_ctx (Task 4b)"
    )
    assert synth_ctx is child_ctxs[0], (
        "synthesizer-phase ctx must equal children's dispatching-iter ctx"
    )


async def test_non_fanout_runs_pass_none_parent_iter_ctx(
    tmp_path: Path,
) -> None:
    """A normal (done) run causes run_span to be opened with
    parent_iter_ctx=None.  Regression guard: threading must not
    accidentally inject a ctx on non-fanout paths.
    """
    settings = Settings(data_dir=tmp_path / ".relay")
    otel = RecordingInstrumentation()
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])
    core = RelayCore(settings, harness=harness, otel=otel)
    await core.start()
    try:
        proj_root = tmp_path / "proj"
        proj_root.mkdir(parents=True, exist_ok=True)
        pid = await core.register_project(proj_root, "p")
        run_id = await core.start_run(pid, "Do the work.")
        result = await core.wait_for_run(run_id)
        assert result.status == "done", result.status
    finally:
        await core.aclose()

    calls = [(rid, ctx) for rid, ctx in otel.calls if rid == run_id]
    assert len(calls) == 1
    assert calls[0][1] is None, (
        f"non-fanout run_span got parent_iter_ctx={calls[0][1]!r}, expected None"
    )
