"""W1 — protocol & normalized event types (spec.md §4.1, ADR-04/18)."""

from __future__ import annotations

import inspect

from relay.harness import (
    AssistantText,
    Harness,
    HarnessEvent,
    HarnessSession,
    SessionEnded,
    SessionStarted,
    SignalConfig,
    SignalEmitted,
    ToolUseEnd,
    ToolUseStart,
    ToolUseUpdate,
)


def test_event_subclasses_carry_base_fields() -> None:
    ev = SessionStarted(seq=1, ts=1.0, session_id="abc", cwd="/tmp")
    assert isinstance(ev, HarnessEvent)
    assert ev.seq == 1 and ev.ts == 1.0
    assert ev.session_id == "abc" and ev.cwd == "/tmp"


def test_assistant_text_kind_defaults_to_text() -> None:
    # spec.md §4.1's two-field constructor must keep working (ADR-18).
    ev = AssistantText(seq=2, ts=2.0, text="hello", turn_seq=1)
    assert ev.kind == "text"
    think = AssistantText(seq=3, ts=3.0, text="r", turn_seq=1, kind="thinking")
    assert think.kind == "thinking"


def test_tool_use_events() -> None:
    s = ToolUseStart(seq=1, ts=0.0, tool_id="t1", name="bash", args={"command": "ls"})
    u = ToolUseUpdate(seq=2, ts=0.0, tool_id="t1", partial_result={"content": []})
    e = ToolUseEnd(
        seq=3,
        ts=0.0,
        tool_id="t1",
        result={"content": []},
        is_error=False,
        duration_ms=5,
    )
    assert s.name == "bash" and u.tool_id == "t1" and e.duration_ms == 5


def test_session_ended_passes_messages_through() -> None:
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}]
    ev = SessionEnded(seq=9, ts=0.0, messages=msgs, stop_reason="clean")
    assert ev.messages is msgs and ev.stop_reason == "clean"


def test_signal_config_defaults() -> None:
    cfg = SignalConfig(strategy="text_sentinels")
    assert cfg.mcp_tool_prefix == "relay__"


def test_signal_emitted_args_default_factory() -> None:
    a, b = SignalEmitted(kind="done"), SignalEmitted(kind="handoff")
    a.args["x"] = 1
    assert b.args == {}  # independent dicts


def test_protocols_are_runtime_checkable_and_async() -> None:
    assert hasattr(Harness, "__instancecheck__")
    assert hasattr(HarnessSession, "__instancecheck__")
    assert inspect.iscoroutinefunction(Harness.spawn)
    assert inspect.iscoroutinefunction(HarnessSession.cancel)
    assert inspect.iscoroutinefunction(HarnessSession.wait)
