"""Unit tests for the join-prompt composition helper (9c).

All offline, all pure-function — no DB, no pi.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from relay_v2.config import Settings
from relay_v2.db import init_db, make_async_engine, make_async_sessionmaker
from relay_v2.orchestrator.lifecycle import (
    close_iter,
    compose_join_prompt,
    create_run,
    latest_fanout_iter,
    open_iter,
)


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


def test_latest_fanout_iter_returns_most_recent_fanout_iter(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()

    async def scenario() -> None:
        engine = make_async_engine(settings.async_db_url)
        sm = make_async_sessionmaker(engine)
        try:
            # Project + run row (project FK satisfied by direct insert).
            async with sm() as s:
                from relay_v2.db.models import Project
                s.add(Project(root_path=str(tmp_path), name="p"))
                await s.commit()
                project = (await s.scalars(
                    __import__("sqlalchemy").select(Project)
                )).one()
            await create_run(
                sm, run_id="r-1", project_id=project.id,
                prompt_body="p", max_iters=4, iter_timeout=60,
                worktree_path=None, branch=None,
            )
            # Iter 1: handoff (not fanout).
            i1 = await open_iter(sm, run_id="r-1", seq=1, phase=None,
                                 prompt="x", preamble="")
            await close_iter(sm, i1, signal_kind="handoff",
                             signal_args={"next_prompt": "y"},
                             exit_reason="signal")
            # Iter 2: fanout — this one should win.
            i2 = await open_iter(sm, run_id="r-1", seq=2, phase=None,
                                 prompt="x", preamble="")
            await close_iter(sm, i2, signal_kind="fanout",
                             signal_args={"payload": {
                                 "children": [
                                     {"role": "a", "prompt": "do a"}
                                 ],
                                 "join_prompt": "merge",
                             }},
                             exit_reason="signal")

            row = await latest_fanout_iter(sm, "r-1")
            assert row is not None
            assert row.seq == 2
            assert row.signal_kind == "fanout"
            assert row.signal_args is not None
            assert row.signal_args["payload"]["join_prompt"] == "merge"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_latest_fanout_iter_none_when_no_fanout(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()

    async def scenario() -> None:
        engine = make_async_engine(settings.async_db_url)
        sm = make_async_sessionmaker(engine)
        try:
            row = await latest_fanout_iter(sm, "nonexistent-run")
            assert row is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
