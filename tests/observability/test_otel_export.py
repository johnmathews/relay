"""Phase 7 verification — OTel span structure, offline (ADR-29).

The "verify span structure (no real network)" half of plan.md Phase 7.
Drives the real :class:`RelayCore` + ``run_loop`` against the scripted
harness (``EventScript``) with an in-memory span exporter and asserts
the ``relay.run`` → ``relay.iter`` → ``relay.tool_call`` tree, the
``relay.iter_seq`` ↔ ``iters.seq`` correlation, the GenAI/usage
attributes (incl. the absent-→-omitted case), and the no-op guarantees
(no provider, no ``OTLPSpanExporter`` construction, no network). The
live-Langfuse-UI half is a documented manual step (docs/observability.md).
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.harness import AssistantText, ToolUseEnd, ToolUseStart
from relay_v2.harness.protocol import SessionEnded
from relay_v2.observability import (
    NOOP,
    NoopInstrumentation,
    OtelInstrumentation,
    build_instrumentation,
)
from relay_v2.observability import otel as otel_mod
from tests.orchestrator.scripted_harness import EventScript, ScriptedHarness, TextScript

DONE = "All done.\n\n[[engteam:done]]"

# pi-shaped assistant usage (verified against the OQ-1 fixture).
USAGE_MESSAGES = [
    {"role": "user", "content": [{"type": "text", "text": "hi"}]},
    {
        "role": "assistant",
        "content": [{"type": "text", "text": DONE}],
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "usage": {
            "input": 3,
            "output": 5,
            "cacheRead": 3075,
            "cacheWrite": 4620,
            "totalTokens": 7703,
            "cost": {"total": 0.0183315},
        },
    },
]


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(data_dir=tmp_path / ".relay", **kw)  # type: ignore[arg-type]


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


def _tool_iter_script(messages: list[object]) -> EventScript:
    """One iter: a tool round-trip, then a done sentinel, then a
    usage-bearing SessionEnded."""
    return EventScript(
        events=[
            ToolUseStart(
                seq=2, ts=1000.0, tool_id="t1", name="Bash",
                args={"command": "ls"},
            ),
            ToolUseEnd(
                seq=3, ts=1002.5, tool_id="t1", result={"ok": True},
                is_error=False, duration_ms=2500,
            ),
            AssistantText(seq=4, ts=1003.0, text=DONE, turn_seq=1,
                           kind="text"),
        ],
        final=SessionEnded(seq=5, ts=1003.1, messages=messages,
                            stop_reason="clean"),
    )


def _spans(exporter: InMemorySpanExporter) -> dict[str, list[object]]:
    by_name: dict[str, list[object]] = {}
    for s in exporter.get_finished_spans():
        by_name.setdefault(s.name, []).append(s)
    return by_name


def test_span_tree_and_usage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([_tool_iter_script(USAGE_MESSAGES)])
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        rid = await core.start_run(pid, "go", max_iters=3)
        result = await core.wait_for_run(rid)
        assert result.status == "done"

    _run(scenario, settings, harness, otel)

    by_name = _spans(exporter)
    assert len(by_name["relay.run"]) == 1
    assert len(by_name["relay.iter"]) == 1
    assert len(by_name["relay.tool_call"]) == 1
    run = by_name["relay.run"][0]
    it = by_name["relay.iter"][0]
    tool = by_name["relay.tool_call"][0]

    # One trace; correct parent chain.
    assert run.parent is None
    assert it.parent is not None and it.parent.span_id == run.context.span_id
    assert tool.parent is not None
    assert tool.parent.span_id == it.context.span_id
    assert run.context.trace_id == it.context.trace_id == tool.context.trace_id

    # iter_seq correlates to the iters table seq (== 1, first iter).
    assert it.attributes["relay.iter_seq"] == 1
    assert it.attributes["relay.run_id"] == run.attributes["relay.run_id"]

    # GenAI / usage on the iter span (set only because pi surfaced them).
    assert it.attributes["gen_ai.system"] == "anthropic"
    assert it.attributes["gen_ai.request.model"] == "claude-sonnet-4-6"
    assert it.attributes["gen_ai.usage.input_tokens"] == 3
    assert it.attributes["gen_ai.usage.output_tokens"] == 5
    assert it.attributes["relay.usage.total_tokens"] == 7703
    assert it.attributes["relay.usage.cache_read_tokens"] == 3075
    assert it.attributes["relay.usage.cache_write_tokens"] == 4620
    assert it.attributes["relay.usage.cost_usd"] == pytest.approx(0.0183315)

    # Tool span carries name (from ToolUseStart) + duration.
    assert tool.attributes["relay.tool_name"] == "Bash"
    assert tool.attributes["relay.tool_id"] == "t1"
    assert tool.attributes["relay.tool_is_error"] is False
    assert tool.attributes["relay.tool_duration_ms"] == 2500


def test_usage_absent_is_omitted_not_zeroed(tmp_path: Path) -> None:
    """pi may not surface usage — absent fields must be omitted, never
    zero-filled (plan.md Phase-7 risk; ADR-29)."""
    settings = _settings(tmp_path)
    no_usage = [
        {"role": "assistant", "content": [{"type": "text", "text": DONE}]}
    ]
    harness = ScriptedHarness([_tool_iter_script(no_usage)])
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        rid = await core.start_run(pid, "go", max_iters=3)
        assert (await core.wait_for_run(rid)).status == "done"

    _run(scenario, settings, harness, otel)

    it = _spans(exporter)["relay.iter"][0]
    for absent in (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.system",
        "gen_ai.request.model",
        "relay.usage.total_tokens",
        "relay.usage.cost_usd",
    ):
        assert absent not in it.attributes


def test_noop_is_default_and_constructs_no_exporter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RELAY_OTEL_EXPORT=none → the literal no-op: no provider, no
    OTLPSpanExporter constructed, no network (ADR-29 risk surface)."""

    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("OTLPSpanExporter must not be constructed")

    monkeypatch.setattr(otel_mod, "OTLPSpanExporter", _boom)

    inst = build_instrumentation(_settings(tmp_path, otel_export="none"))
    assert inst is NOOP
    assert isinstance(inst, NoopInstrumentation)
    # The no-op holds no SDK provider at all.
    assert not hasattr(inst, "_provider")


def test_noop_run_completes_without_spans(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([_tool_iter_script(USAGE_MESSAGES)])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        rid = await core.start_run(pid, "go", max_iters=3)
        assert (await core.wait_for_run(rid)).status == "done"

    _run(scenario, settings, harness, NOOP)  # no exporter exists at all


def test_langfuse_misconfig_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="langfuse"):
        build_instrumentation(
            _settings(tmp_path, otel_export="langfuse")  # keys unset
        )


def test_langfuse_endpoint_and_auth_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The OTLP endpoint + Basic-auth header are built per the Langfuse
    self-hosted contract (researched in ADR-29, not guessed)."""
    captured: dict[str, object] = {}

    def _capture(*, endpoint: str, headers: dict[str, str]) -> object:
        captured["endpoint"] = endpoint
        captured["headers"] = headers
        # Return a real exporter so the (private) provider built around
        # it has a valid .shutdown() — keeps the atexit hook quiet
        # without weakening what this test asserts.
        return InMemorySpanExporter()

    monkeypatch.setattr(otel_mod, "OTLPSpanExporter", _capture)

    build_instrumentation(
        _settings(
            tmp_path,
            otel_export="langfuse",
            langfuse_host="http://localhost:3000/",
            langfuse_public_key="pk-lf-abc",
            langfuse_secret_key="sk-lf-xyz",
        )
    )

    assert captured["endpoint"] == (
        "http://localhost:3000/api/public/otel/v1/traces"
    )
    expected = base64.b64encode(b"pk-lf-abc:sk-lf-xyz").decode()
    assert captured["headers"]["Authorization"] == f"Basic {expected}"


# ── Task 1: IterSpanContext carrier ────────────────────────────────────────


def test_noop_iter_span_context_is_none() -> None:
    """NOOP path: IterSpan.context is None — class attribute, no provider
    constructed (ADR-29 risk surface)."""
    with NOOP.run_span("r1") as run_span:
        with run_span.iter_span(seq=1, phase=None) as iter_span:
            assert iter_span.context is None


def test_otel_iter_span_context_carries_trace_identity() -> None:
    """OTel path: iter_span.context is non-None and a new span constructed
    with it as parent has parent.span_id == the iter span's span_id."""
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    captured_ctx = None
    iter_span_id = None

    with otel.run_span("r-ctx-test") as run_span:
        with run_span.iter_span(seq=1, phase=None) as iter_span:
            captured_ctx = iter_span.context
            # Peek at the internal span to record its span_id for assertion.
            # _OtelIterSpan stores the span as self._span.
            iter_span_id = iter_span._span.get_span_context().span_id  # type: ignore[union-attr]

    # iter_span.context must be non-None on the OTel path.
    assert captured_ctx is not None

    # Build a child span using the captured context; it must parent to the iter.
    tracer = otel._tracer  # type: ignore[attr-defined]
    child = tracer.start_span("test.child", context=captured_ctx)
    child.end()

    finished = {s.name: s for s in exporter.get_finished_spans()}
    assert "test.child" in finished
    child_span = finished["test.child"]
    iter_span_finished = finished["relay.iter"]
    assert child_span.parent is not None
    assert child_span.parent.span_id == iter_span_id
    assert child_span.context.trace_id == iter_span_finished.context.trace_id


# ── Task 2: parent_iter_ctx kwarg on Instrumentation.run_span ─────────────


def test_otel_run_span_accepts_parent_iter_ctx_kwarg_default_none() -> None:
    """Opening run_span with no kwarg still produces a root span (no parent).
    This asserts the default behaviour is unchanged."""
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    with otel.run_span("r-default") as _run_span:
        pass

    finished = exporter.get_finished_spans()
    run_spans = [s for s in finished if s.name == "relay.run"]
    assert len(run_spans) == 1
    assert run_spans[0].parent is None  # root span — no parent


def test_otel_run_span_parents_under_parent_iter_ctx() -> None:
    """Open a run+iter span on parent run 'P'; capture iter_span.context;
    close the iter+run; then call run_span('C', parent_iter_ctx=captured).
    Assert C run-span's parent.span_id == P_iter.span_id and trace_id matches."""
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    captured_ctx = None
    p_iter_span_id = None
    p_trace_id = None

    # Open parent run 'P' and capture iter context.
    with otel.run_span("P") as p_run_span:
        with p_run_span.iter_span(seq=1, phase=None) as p_iter_span:
            captured_ctx = p_iter_span.context
            p_iter_span_id = p_iter_span._span.get_span_context().span_id  # type: ignore[union-attr]
            p_trace_id = p_iter_span._span.get_span_context().trace_id  # type: ignore[union-attr]

    assert captured_ctx is not None

    # Open child run 'C' with the captured iter context as parent.
    with otel.run_span("C", parent_iter_ctx=captured_ctx) as _c_run_span:
        pass

    c_run_spans = [
        s for s in exporter.get_finished_spans()
        if s.name == "relay.run" and s.attributes.get("relay.run_id") == "C"
    ]
    assert len(c_run_spans) == 1
    c_run = c_run_spans[0]

    # The C run-span must be parented under P's iter span.
    assert c_run.parent is not None
    assert c_run.parent.span_id == p_iter_span_id
    # Trace continuity: C run-span must share the same trace as P.
    assert c_run.context.trace_id == p_trace_id


def test_noop_run_span_ignores_parent_iter_ctx() -> None:
    """NOOP.run_span accepts parent_iter_ctx=<anything> without raising and
    returns the same NOOP RunSpan. Locks NOOP signature compatibility."""
    with NOOP.run_span("r1", parent_iter_ctx=object()) as run_span:
        # The noop run span must still be functional (iter_span works).
        with run_span.iter_span(seq=1, phase=None) as iter_span:
            assert iter_span.context is None


# ── Task 4b: synthesizer-phase run-span parenting ─────────────────────────


_FANOUT_TWO = (
    "Dispatching two children.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": ['
    '{"role": "worker-a", "prompt": "Do task A."},'
    '{"role": "worker-b", "prompt": "Do task B."}'
    '],'
    '"join_prompt": "Synthesize the results."'
    "}\n"
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
_CHILD_DONE = "Work complete.\n\n[[engteam:done]]"
_SYNTH_DONE = "Synthesis complete.\n\n[[engteam:done]]"


def test_synthesizer_phase_runspan_is_parented_under_dispatching_iter(
    tmp_path: Path,
) -> None:
    """After Task 4b, the parent's synthesizer-phase relay.run span must be
    parented under the dispatching iter span (same fanout iter that the child
    run-spans parent under).  The entire fanout-join sub-tree shares one
    trace_id rooted at the pre-fanout relay.run span.

    Before Task 4b the synthesizer-phase relay.run is a disconnected root.
    After 4b it has parent.span_id == dispatching_iter.span_id. ADR-38.
    """
    # Git-init so child runs can provision real worktrees (branch fields).
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    settings = _settings(tmp_path)
    harness = ScriptedHarness([
        TextScript(_FANOUT_TWO),
        TextScript(_CHILD_DONE),
        TextScript(_CHILD_DONE),
        TextScript(_SYNTH_DONE),
    ])
    exporter = InMemorySpanExporter()
    otel = OtelInstrumentation(SimpleSpanProcessor(exporter))

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Investigate.")
        # First settle: parent enters awaiting_children.
        first = await core.wait_for_run(parent_id)
        assert first.status == "awaiting_children", first.status
        # Fetch children via the RelayCore API (9e).
        children = await core.list_children(parent_id)
        assert len(children) == 2
        # Drain children.
        for child in children:
            await core.wait_for_run(child.id)
        # Second settle: synthesizer iter completes → done.
        second = await core.wait_for_run(parent_id)
        assert second.status == "done", second.status
        return parent_id

    parent_id = _run(scenario, settings, harness, otel)

    finished = exporter.get_finished_spans()

    # ── find the two relay.run spans for the parent run ────────────────────
    parent_run_spans = [
        s for s in finished
        if s.name == "relay.run"
        and s.attributes.get("relay.run_id") == parent_id
    ]
    assert len(parent_run_spans) == 2, (
        f"expected exactly 2 relay.run spans for parent run, "
        f"got {len(parent_run_spans)}"
    )

    # Pre-fanout phase: the root run span (no parent).
    pre_fanout_spans = [s for s in parent_run_spans if s.parent is None]
    synth_spans = [s for s in parent_run_spans if s.parent is not None]
    assert len(pre_fanout_spans) == 1, (
        "expected exactly one root relay.run span for the pre-fanout phase"
    )
    assert len(synth_spans) == 1, (
        "expected exactly one non-root relay.run span for the synth phase "
        "(Task 4b not yet implemented — synth-phase span is still a root)"
    )
    pre_fanout_run = pre_fanout_spans[0]
    synth_run = synth_spans[0]

    # ── find the dispatching iter span ────────────────────────────────────
    # The dispatching iter is the relay.iter span whose parent is the
    # pre-fanout relay.run span.  It is the fanout-signal iter (seq==1 on
    # the parent run).
    parent_iter_spans = [
        s for s in finished
        if s.name == "relay.iter"
        and s.attributes.get("relay.run_id") == parent_id
    ]
    dispatching_iters = [
        s for s in parent_iter_spans
        if s.parent is not None
        and s.parent.span_id == pre_fanout_run.context.span_id
    ]
    assert len(dispatching_iters) == 1, (
        f"expected exactly one relay.iter child of the pre-fanout run span, "
        f"got {len(dispatching_iters)}"
    )
    dispatching_iter = dispatching_iters[0]

    # ── core assertion: synth-phase parents under dispatching iter ─────────
    # ADR-38: synth-phase run-span parents under the same dispatching iter
    # as the children (one connected fanout-join sub-tree).
    assert synth_run.parent.span_id == dispatching_iter.context.span_id, (
        "synthesizer-phase relay.run span must parent under the dispatching "
        f"iter span (Task 4b).  Got parent.span_id="
        f"{synth_run.parent.span_id!r}, expected "
        f"{dispatching_iter.context.span_id!r}"
    )

    # All spans share the same trace (one connected tree).
    assert (
        pre_fanout_run.context.trace_id
        == synth_run.context.trace_id
        == dispatching_iter.context.trace_id
    ), "all spans in the fanout-join cycle must share the same trace_id"
