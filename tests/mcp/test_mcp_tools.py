"""Phase-5 MCP tool unit tests (spec §8, ADR-27).

Each tool is a thin ``RelayCore`` adapter; these tests drive them
in-process via ``FastMCP.call_tool`` (no transport) against a real
``RelayCore`` wired to a scripted harness — the ``tests/orchestrator/``
convention (``asyncio.run`` + ``Settings(data_dir=tmp_path/".relay")``
+ ``ScriptedHarness``), per CLAUDE.md/ADR-24.

``FastMCP.call_tool`` returns ``(content_blocks, structured)``. For a
tool returning a Pydantic model, ``structured`` is that model's dict;
for a ``list[...]`` return it is ``{"result": [...]}``. Tool exceptions
surface as ``mcp.server.fastmcp.exceptions.ToolError`` whose message
embeds the underlying ``ValueError`` text.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.mcp import create_mcp_server
from tests.orchestrator.scripted_harness import (
    HangScript,
    ScriptedHarness,
    TextScript,
)

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"
PAUSE_BLOCK = (
    "I need a decision.\n\n"
    "[[engteam:prompt-start]]\n"
    "Proceed with the chosen option.\n"
    "[[engteam:prompt-end]]\n\n"
    '[[engteam:pause-for-input id="P1" question="Use A or B?"]]'
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


def _run[T](
    coro: Callable[[RelayCore, FastMCP], Awaitable[T]],
    settings: Settings,
    harness: ScriptedHarness | None = None,
) -> T:
    async def _main() -> T:
        core = RelayCore(
            settings,
            harness=harness or ScriptedHarness([TextScript(DONE_BLOCK)]),
        )
        await core.start()
        try:
            mcp = create_mcp_server(core)
            return await coro(core, mcp)
        finally:
            await core.aclose()

    return asyncio.run(_main())


async def _structured(mcp: FastMCP, name: str, **args: Any) -> Any:
    """Call a tool and return its structured payload, unwrapping the
    ``{"result": [...]}`` envelope FastMCP adds for list returns."""
    _content, structured = await mcp.call_tool(name, args)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


# ── tool registration ──────────────────────────────────────────────────


def test_all_seven_tools_registered(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        names = sorted(t.name for t in await mcp.list_tools())
        assert names == sorted(
            [
                "relay__list_runs",
                "relay__get_run",
                "relay__start_run",
                "relay__cancel_run",
                "relay__pause_response",
                "relay__tail_events",
                "relay__read_artifact",
            ]
        )

    _run(scenario, settings)


# ── list / get ─────────────────────────────────────────────────────────


def test_list_and_get_run_happy_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        await core.register_project(proj, "demo")
        run_id = await core.start_run(
            (await core.list_projects())[0].id, "Go."
        )
        await core.wait_for_run(run_id)

        all_runs = await _structured(mcp, "relay__list_runs")
        assert [r["id"] for r in all_runs] == [run_id]

        scoped = await _structured(
            mcp, "relay__list_runs", project_root=str(proj)
        )
        assert [r["id"] for r in scoped] == [run_id]

        detail = await _structured(mcp, "relay__get_run", run_id=run_id)
        assert detail["id"] == run_id
        assert detail["status"] == "done"
        assert isinstance(detail["iters"], list) and detail["iters"]

    _run(scenario, settings)


def test_list_runs_includes_child_runs(tmp_path: Path) -> None:
    """relay__list_runs passes include_children=True, so child runs appear.

    Creates a parent run, inserts a child run row directly via the DB layer
    (mirroring _make_child_run from tests/orchestrator/test_relay_core.py),
    then asserts both parent and child are returned by the MCP tool.  A
    regression where include_children=True is silently dropped would cause
    the child to be absent from the result.
    """
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        from relay_v2.orchestrator.lifecycle import create_run

        project_id = await core.register_project(proj, "demo")
        parent_id = await core.start_run(project_id, "parent task")
        await core.wait_for_run(parent_id)

        # Insert a child run row directly — no fanout sentinel needed; we are
        # testing that list_runs surfaces child rows, not that dispatch works.
        child_id = core._new_run_id()
        await create_run(
            core._sm,
            run_id=child_id,
            project_id=project_id,
            prompt_body="child task",
            max_iters=1,
            iter_timeout=60,
            worktree_path=None,
            branch=None,
            parent_run_id=parent_id,
        )

        all_runs = await _structured(mcp, "relay__list_runs")
        returned_ids = {r["id"] for r in all_runs}
        assert parent_id in returned_ids, "parent run must appear"
        assert child_id in returned_ids, "child run must appear (include_children=True)"

    _run(scenario, settings)


def test_list_runs_unknown_project_root_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        with pytest.raises(ToolError, match="no registered project"):
            await mcp.call_tool(
                "relay__list_runs", {"project_root": "/nope/missing"}
            )

    _run(scenario, settings)


def test_get_run_unknown_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        with pytest.raises(ToolError, match="unknown run nope"):
            await mcp.call_tool("relay__get_run", {"run_id": "nope"})

    _run(scenario, settings)


# ── start ──────────────────────────────────────────────────────────────


def test_start_run_via_tool(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        await core.register_project(proj, "demo")
        run = await _structured(
            mcp,
            "relay__start_run",
            project_root=str(proj),
            prompt="Do the thing.",
            max_iters=3,
        )
        assert run["max_iters"] == 3
        assert run["prompt_body"] == "Do the thing."
        result = await core.wait_for_run(run["id"])
        assert result.status == "done"

    _run(scenario, settings)


def test_start_run_unknown_root_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        with pytest.raises(ToolError, match="no registered project"):
            await mcp.call_tool(
                "relay__start_run",
                {"project_root": str(tmp_path / "x"), "prompt": "hi"},
            )

    _run(scenario, settings)


# ── cancel ─────────────────────────────────────────────────────────────


def test_cancel_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    harness = ScriptedHarness([HangScript()])

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        pid = await core.register_project(proj, "demo")
        run_id = await core.start_run(pid, "Hang.")
        await asyncio.wait_for(harness.blocked.wait(), timeout=5)

        out = await _structured(mcp, "relay__cancel_run", run_id=run_id)
        assert out["id"] == run_id
        result = await core.wait_for_run(run_id)
        assert result.status == "cancelled"

    _run(scenario, settings, harness)


def test_cancel_unknown_run_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        with pytest.raises(ToolError, match="unknown run ghost"):
            await mcp.call_tool("relay__cancel_run", {"run_id": "ghost"})

    _run(scenario, settings)


# ── pause / resume ─────────────────────────────────────────────────────


def test_pause_response_resumes_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    harness = ScriptedHarness(
        [TextScript(PAUSE_BLOCK), TextScript(DONE_BLOCK)]
    )

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        pid = await core.register_project(proj, "demo")
        run_id = await core.start_run(pid, "Start.")
        first = await core.wait_for_run(run_id)
        assert first.status == "paused"

        out = await _structured(
            mcp, "relay__pause_response", run_id=run_id, answer="Use A"
        )
        assert out["id"] == run_id
        second = await core.wait_for_run(run_id)
        assert second.status == "done"

    _run(scenario, settings, harness)


def test_pause_response_on_non_paused_run_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        pid = await core.register_project(proj, "demo")
        run_id = await core.start_run(pid, "Go.")
        await core.wait_for_run(run_id)  # status=done, not paused
        with pytest.raises(ToolError, match="is not paused"):
            await mcp.call_tool(
                "relay__pause_response",
                {"run_id": run_id, "answer": "x"},
            )

    _run(scenario, settings)


# ── tail_events ────────────────────────────────────────────────────────


def test_tail_events_snapshot_and_cursor(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        pid = await core.register_project(proj, "demo")
        run_id = await core.start_run(pid, "Go.")
        await core.wait_for_run(run_id)

        events = await _structured(
            mcp, "relay__tail_events", run_id=run_id
        )
        assert events, "a completed run must have emitted events"
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs)

        # since_seq cursor: nothing strictly after the last seq.
        tail = await _structured(
            mcp, "relay__tail_events", run_id=run_id, since_seq=seqs[-1]
        )
        assert tail == []

    _run(scenario, settings)


def test_tail_events_unknown_run_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        with pytest.raises(ToolError, match="unknown run zzz"):
            await mcp.call_tool("relay__tail_events", {"run_id": "zzz"})

    _run(scenario, settings)


# ── read_artifact ──────────────────────────────────────────────────────


def test_read_artifact_happy_and_sandbox(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore, mcp: FastMCP) -> None:
        pid = await core.register_project(proj, "demo")
        run_id = await core.start_run(pid, "Go.")
        await core.wait_for_run(run_id)

        run_dir = proj / ".relay" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.md").write_text("# hello\nbody\n")

        content, _structured_unused = await mcp.call_tool(
            "relay__read_artifact",
            {"run_id": run_id, "path": "report.md"},
        )
        assert content[0].text == "# hello\nbody\n"

        with pytest.raises(ToolError, match="invalid artifact path"):
            await mcp.call_tool(
                "relay__read_artifact",
                {"run_id": run_id, "path": "../../etc/passwd"},
            )

        with pytest.raises(ToolError, match="artifact not found"):
            await mcp.call_tool(
                "relay__read_artifact",
                {"run_id": run_id, "path": "missing.md"},
            )

        from relay_v2.api.files import MAX_FILE_BYTES

        # Oversize: size check fires before binary read, so a text-shaped
        # file just over the cap returns the size error (not OOM-alloc).
        (run_dir / "big.md").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
        with pytest.raises(ToolError, match="too large"):
            await mcp.call_tool(
                "relay__read_artifact",
                {"run_id": run_id, "path": "big.md"},
            )

        # Binary: NUL byte in the first 8 KiB triggers the binary guard.
        (run_dir / "bin.dat").write_bytes(b"\x00\x01\x02")
        with pytest.raises(ToolError, match="binary"):
            await mcp.call_tool(
                "relay__read_artifact",
                {"run_id": run_id, "path": "bin.dat"},
            )

    _run(scenario, settings)


def test_read_artifact_unknown_run_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(_core: RelayCore, mcp: FastMCP) -> None:
        with pytest.raises(ToolError, match="unknown run absent"):
            await mcp.call_tool(
                "relay__read_artifact",
                {"run_id": "absent", "path": "x.md"},
            )

    _run(scenario, settings)
