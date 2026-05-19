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
from tests.orchestrator.scripted_harness import EventScript, ScriptedHarness

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
