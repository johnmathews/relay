"""Unit tests for the join-prompt composition helper (9c).

All offline, all pure-function — no DB, no pi.
"""
from __future__ import annotations

from relay_v2.orchestrator.lifecycle import compose_join_prompt


def test_compose_join_prompt_two_children_done() -> None:
    body = compose_join_prompt(
        "Synthesize the two audits and propose a unified fix list.",
        [
            {
                "id": "20260521-100000-aa",
                "role": "explorer-frontend",
                "status": "done",
                "summary": "Found 3 router bugs.",
                "branch": "relay/20260521-100000-aa",
                "worktree_path": "/tmp/.relay/worktrees/20260521-100000-aa",
            },
            {
                "id": "20260521-100000-bb",
                "role": "explorer-backend",
                "status": "done",
                "summary": "Found 2 schema drift issues.",
                "branch": "relay/20260521-100000-bb",
                "worktree_path": "/tmp/.relay/worktrees/20260521-100000-bb",
            },
        ],
    )
    assert body.startswith(
        "Synthesize the two audits and propose a unified fix list."
    )
    assert "RELAY_CHILD_RESULTS:" in body
    assert "- id: 20260521-100000-aa" in body
    assert "  role: explorer-frontend" in body
    assert "  status: done" in body
    assert "  summary: Found 3 router bugs." in body
    assert "  branch: relay/20260521-100000-aa" in body
    assert "  worktree_path: /tmp/.relay/worktrees/20260521-100000-aa" in body
    assert "- id: 20260521-100000-bb" in body
    assert "  role: explorer-backend" in body


def test_compose_join_prompt_preserves_join_prompt_first() -> None:
    body = compose_join_prompt(
        "Custom join instructions.",
        [{"id": "x", "role": "r", "status": "done", "summary": "s",
          "branch": "b", "worktree_path": "/p"}],
    )
    lines = body.split("\n")
    assert lines[0] == "Custom join instructions."
    # Separator + trailer header come after the join prompt.
    sep_idx = lines.index("---")
    trailer_idx = lines.index("RELAY_CHILD_RESULTS:")
    assert sep_idx < trailer_idx


def test_compose_join_prompt_one_child_mixed_status() -> None:
    body = compose_join_prompt(
        "Decide what to do.",
        [
            {"id": "a", "role": "r-a", "status": "done", "summary": "ok",
             "branch": "relay/a", "worktree_path": "/wt/a"},
            {"id": "b", "role": "r-b", "status": "cancelled",
             "summary": "user cancelled", "branch": "relay/b",
             "worktree_path": "/wt/b"},
            {"id": "c", "role": "r-c", "status": "failed",
             "summary": "timeout", "branch": "relay/c",
             "worktree_path": "/wt/c"},
        ],
    )
    assert "  status: done" in body
    assert "  status: cancelled" in body
    assert "  status: failed" in body
    # All three children rendered.
    assert body.count("- id: ") == 3


def test_compose_join_prompt_empty_summary_renders_as_empty_string() -> None:
    body = compose_join_prompt(
        "j",
        [{"id": "a", "role": "r", "status": "done", "summary": "",
          "branch": "relay/a", "worktree_path": "/wt/a"}],
    )
    # Empty summary still appears as 'summary:' — never omitted, to keep
    # the YAML-ish block uniform for the skill reader.
    assert "  summary: " in body


def test_compose_join_prompt_multiline_summary_indented() -> None:
    body = compose_join_prompt(
        "j",
        [{"id": "a", "role": "r", "status": "done",
          "summary": "line one\nline two", "branch": "relay/a",
          "worktree_path": "/wt/a"}],
    )
    # Multi-line summary uses YAML literal block to preserve newlines
    # without forcing the skill to handle ad-hoc escapes.
    assert "  summary: |" in body
    assert "    line one" in body
    assert "    line two" in body
