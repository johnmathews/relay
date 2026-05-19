"""W2 — pi JSONL → normalized HarnessEvent mapping (spec.md §4.2).

Runs fully offline against the committed de-risking fixtures. Resolves
OQ-1 (agent_end.messages shape) and OQ-2 (delta accumulation) with the
captured streams as ground truth, not assumptions.
"""

from __future__ import annotations

from relay_v2.harness import (
    AssistantText,
    SessionEnded,
    SessionStarted,
    ToolUseEnd,
    ToolUseStart,
    ToolUseUpdate,
)
from relay_v2.harness.pi import map_pi_events

from .conftest import load_jsonl


def test_simple_completion_maps_to_normalized_stream() -> None:
    events = map_pi_events(load_jsonl("test_simple_completion.jsonl"))

    assert isinstance(events[0], SessionStarted)
    assert events[0].session_id == "019e3f8f-ca50-775c-8de2-49d51a7d9c3f"
    assert events[0].cwd.endswith("pi_derisk_workdir")

    texts = [e for e in events if isinstance(e, AssistantText)]
    # OQ-2: text_delta accumulated into ONE AssistantText per turn.
    assert [t.text for t in texts if t.kind == "text"] == ["pong"]
    assert all(t.turn_seq == 1 for t in texts)

    assert isinstance(events[-1], SessionEnded)
    assert events[-1].stop_reason == "clean"


def test_seq_is_monotonic() -> None:
    events = map_pi_events(load_jsonl("test_event_shapes.jsonl"))
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_oq1_agent_end_messages_passed_through_verbatim() -> None:
    raw = load_jsonl("test_simple_completion.jsonl")
    agent_end = next(e for e in raw if e["type"] == "agent_end")
    events = map_pi_events(raw)
    ended = events[-1]
    assert isinstance(ended, SessionEnded)
    # Harness does not interpret the compiled message list (OQ-1).
    assert ended.messages == agent_end["messages"]
    last = ended.messages[-1]
    assert last["role"] == "assistant"
    assert last["content"][0]["text"] == "pong"
    # Bonus (OQ-3 hint): pi surfaces token + cost in usage.
    assert "usage" in last and "cost" in last["usage"]


def test_event_shapes_tool_execution_mapping() -> None:
    events = map_pi_events(load_jsonl("test_event_shapes.jsonl"))

    starts = [e for e in events if isinstance(e, ToolUseStart)]
    updates = [e for e in events if isinstance(e, ToolUseUpdate)]
    ends = [e for e in events if isinstance(e, ToolUseEnd)]

    assert len(starts) == 3 and len(ends) == 3 and len(updates) == 6
    s0 = starts[0]
    assert s0.name == "bash" and s0.args == {"command": "echo hello"}
    e0 = ends[0]
    assert e0.tool_id == s0.tool_id
    assert e0.is_error is False
    assert e0.result == {"content": [{"type": "text", "text": "hello\n"}]}
    assert e0.duration_ms >= 0


def test_thinking_surfaced_separately_from_text() -> None:
    # ADR-18: thinking_delta accumulates into AssistantText(kind="thinking"),
    # kept distinct from response text so signaling can ignore reasoning.
    events = map_pi_events(load_jsonl("test_event_shapes.jsonl"))
    kinds = {t.kind for t in events if isinstance(t, AssistantText)}
    assert "thinking" in kinds and "text" in kinds
    thinking = [
        t for t in events if isinstance(t, AssistantText) and t.kind == "thinking"
    ]
    assert any("echo hello" in t.text for t in thinking)


def test_no_agent_end_yields_no_sessionended_in_pure_mapping() -> None:
    # long_bash was killed by the de-risk harness's own 180s wall-clock;
    # the stream has NO agent_end. Pure mapping must not fabricate one —
    # synthesizing the terminal event is PiSession.wait()'s job.
    events = map_pi_events(load_jsonl("test_long_bash.jsonl"))
    assert not any(isinstance(e, SessionEnded) for e in events)
    assert isinstance(events[0], SessionStarted)


def test_unknown_event_types_are_ignored_gracefully() -> None:
    raw = [
        {"type": "session", "id": "x", "cwd": "/tmp"},
        {"type": "totally_new_pi_event", "payload": 1},
        {"type": "message_update", "assistantMessageEvent": {"type": "mystery_delta"}},
        {"type": "agent_end", "messages": []},
    ]
    events = map_pi_events(raw)
    assert isinstance(events[0], SessionStarted)
    assert isinstance(events[-1], SessionEnded)
    assert len(events) == 2  # the two unknowns produced nothing


def test_session_resume_fixtures_share_session_id() -> None:
    """W7: the two captured resume invocations exist as ground truth but
    were never asserted. Both map to a SessionStarted, and pi carries the
    same session id across the resume (findings.md)."""
    run1 = map_pi_events(load_jsonl("test_session_resume_run1.jsonl"))
    run2 = map_pi_events(load_jsonl("test_session_resume_run2.jsonl"))
    assert isinstance(run1[0], SessionStarted)
    assert isinstance(run2[0], SessionStarted)
    assert run1[0].session_id == "019e3f92-d7ee-70de-b1ff-89ad7d681ee7"
    assert run2[0].session_id == run1[0].session_id
