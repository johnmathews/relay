"""Phase 14e — `relay.pause.artifacts_edited_count` on the resumed iter span.

The orchestrator pre-counts `artifact_edited` events scoped to the paused
predecessor iter and passes the count via `RunSpan.iter_span(...,
pause_artifacts_edited_count=)`; the OTel mirror sets the attribute on
the resumed iter's `relay.iter` span at start time. Subsequent iters
must not carry the attribute. NOOP `Instrumentation` accepts and ignores
the kwarg (no provider / exporter / network — same shape as the 9f
`parent_iter_ctx` kwarg).

Verification uses an `InMemorySpanExporter` driven by the real
`RelayCore` + `run_loop` against the scripted harness, mirroring the
Phase-7 test layout (`test_otel_export.py`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from relay.config import Settings
from relay.core import RelayCore
from relay.observability import NOOP, OtelInstrumentation
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

PAUSE_BLOCK = (
    "I need a decision.\n\n"
    "[[engteam:prompt-start]]\n"
    "Proceed with the chosen option.\n"
    "[[engteam:prompt-end]]\n\n"
    '[[engteam:pause-for-input id="P1" question="Use A or B?"'
    ' review_path="plan.md"]]'
)
DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")  # type: ignore[call-arg]


def _run[T](
    coro: Callable[[RelayCore], Awaitable[T]],
    settings: Settings,
    harness: ScriptedHarness,
    otel: object,
) -> T:
    async def _main() -> T:
        core = RelayCore(settings, harness=harness, otel=otel)  # type: ignore[arg-type]
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()

    return asyncio.run(_main())


async def _run_pause_resume_with_edits(
    core: RelayCore, tmp_path: Path, n_edits: int
) -> str:
    """Drive one pause/resume cycle, seeding ``n_edits`` `artifact_edited`
    events scoped to the paused iter so the resumed iter's OTel span
    can carry the pre-computed count."""
    pid = await core.register_project(tmp_path, "p")
    run_id = await core.start_run(pid, "Start.")
    paused = await core.wait_for_run(run_id)
    assert paused.status == "paused"
    # The 14a write endpoint normally appends `artifact_edited`; for this
    # span-attribute test we seed the count directly via `store_event`
    # using the paused iter's id (the same iter_id the loop's count query
    # filters on). This keeps the test orthogonal to the 14a HTTP path.
    from relay.orchestrator.lifecycle import latest_paused_iter

    iter_row = await latest_paused_iter(core._sm, run_id)  # type: ignore[attr-defined]
    assert iter_row is not None
    for i in range(n_edits):
        await core.store_event(
            run_id,
            "artifact_edited",
            {
                "path": "plan.md",
                "size_before": 1 + i,
                "size_after": 2 + i,
                "sha256_before": f"old{i}",
                "sha256_after": f"new{i}",
                "editor": "dashboard",
            },
            iter_id=iter_row.id,
        )
    await core.resume_run(run_id, "Use option A.")
    second = await core.wait_for_run(run_id)
    assert second.status == "done"
    return run_id


def _resumed_iter_span(exporter: InMemorySpanExporter) -> object:
    """The resumed iter is the SECOND `relay.iter` span (seq=2) in a
    pause→resume→done cycle. Returned by iter_seq lookup (more robust
    than list ordering in case the SimpleSpanProcessor reorders)."""
    by_seq: dict[int, object] = {}
    for s in exporter.get_finished_spans():
        if s.name == "relay.iter":
            seq = s.attributes.get("relay.iter_seq")
            assert isinstance(seq, int)
            by_seq[seq] = s
    return by_seq[2]


def _paused_iter_span(exporter: InMemorySpanExporter) -> object:
    by_seq: dict[int, object] = {}
    for s in exporter.get_finished_spans():
        if s.name == "relay.iter":
            seq = s.attributes.get("relay.iter_seq")
            assert isinstance(seq, int)
            by_seq[seq] = s
    return by_seq[1]


def test_resumed_iter_span_carries_zero_count_when_no_edits(
    tmp_path: Path,
) -> None:
    """Resume with no `artifact_edited` events between pause and resume:
    the resumed iter's span carries `relay.pause.artifacts_edited_count = 0`."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(PAUSE_BLOCK), TextScript(DONE_BLOCK)]
    )
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    async def scenario(core: RelayCore) -> None:
        await _run_pause_resume_with_edits(core, tmp_path, n_edits=0)

    _run(scenario, settings, harness, otel)
    span = _resumed_iter_span(exporter)
    assert span.attributes["relay.pause.artifacts_edited_count"] == 0  # type: ignore[attr-defined]
    # The paused iter's span (seq=1) must NOT carry the attribute — it
    # belongs to the resumed iter only.
    paused = _paused_iter_span(exporter)
    assert (
        "relay.pause.artifacts_edited_count" not in paused.attributes  # type: ignore[attr-defined]
    )


def test_resumed_iter_span_carries_one_edit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(PAUSE_BLOCK), TextScript(DONE_BLOCK)]
    )
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    async def scenario(core: RelayCore) -> None:
        await _run_pause_resume_with_edits(core, tmp_path, n_edits=1)

    _run(scenario, settings, harness, otel)
    span = _resumed_iter_span(exporter)
    assert span.attributes["relay.pause.artifacts_edited_count"] == 1  # type: ignore[attr-defined]


def test_resumed_iter_span_carries_three_edits(tmp_path: Path) -> None:
    """Three edits between pause and resume → count == 3 on the
    resumed iter span."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(PAUSE_BLOCK), TextScript(DONE_BLOCK)]
    )
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    async def scenario(core: RelayCore) -> None:
        await _run_pause_resume_with_edits(core, tmp_path, n_edits=3)

    _run(scenario, settings, harness, otel)
    span = _resumed_iter_span(exporter)
    assert span.attributes["relay.pause.artifacts_edited_count"] == 3  # type: ignore[attr-defined]


def test_noop_path_accepts_pause_kwarg(tmp_path: Path) -> None:
    """The literal no-op `Instrumentation` accepts and ignores the new
    `pause_artifacts_edited_count` kwarg — no provider / exporter
    constructed, no network call, no error on a resumed iter."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(PAUSE_BLOCK), TextScript(DONE_BLOCK)]
    )

    async def scenario(core: RelayCore) -> None:
        await _run_pause_resume_with_edits(core, tmp_path, n_edits=2)

    _run(scenario, settings, harness, NOOP)
