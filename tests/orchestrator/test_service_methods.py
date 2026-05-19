"""W1: RelayCore service-layer read/CRUD methods (Phase 3 prep).

Phase 3's REST routes are thin adapters over ``RelayCore`` (ADR-07/
ADR-15). This locks down the service methods the routes will call:
project read/unregister, versioned prompt CRUD, event/iter replay reads,
and the side-effect-free run preview. Pattern matches the rest of
``tests/orchestrator/``: ``asyncio.run`` (no pytest-asyncio yet),
``Settings(data_dir=tmp_path/".relay")``, and a throwaway sync engine
for orchestrator-independent read-back assertions (ADR-10).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Prompt, Run
from tests.orchestrator.scripted_harness import (
    ScriptedHarness,
    TextScript,
)

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


def _read(settings: Settings) -> Session:
    engine = create_engine(settings.db_url)
    return Session(engine)


def _run[T](
    coro: Callable[[RelayCore], Awaitable[T]],
    settings: Settings,
    harness: ScriptedHarness | None = None,
) -> T:
    async def _main() -> T:
        core = RelayCore(
            settings, harness=harness or ScriptedHarness([TextScript(DONE_BLOCK)])
        )
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()

    return asyncio.run(_main())


# ── projects ───────────────────────────────────────────────────────────


def test_project_list_get_delete(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    p_a = tmp_path / "a"
    p_b = tmp_path / "b"
    p_a.mkdir()
    p_b.mkdir()

    async def scenario(core: RelayCore) -> None:
        id_a = await core.register_project(p_a, "alpha")
        id_b = await core.register_project(p_b, "beta")

        projects = await core.list_projects()
        assert [p.id for p in projects] == sorted([id_a, id_b])

        one = await core.get_project(id_a)
        assert one is not None and one.name == "alpha"
        assert await core.get_project(9999) is None

        # delete only the row; the directory must remain on disk.
        assert await core.delete_project(id_a) is True
        assert await core.get_project(id_a) is None
        assert p_a.exists()  # files untouched (spec.md §7)
        assert await core.delete_project(id_a) is False  # unknown id
        assert await core.delete_project(9999) is False

        remaining = await core.list_projects()
        assert [p.id for p in remaining] == [id_b]

    _run(scenario, settings)


# ── prompts (versioned) ────────────────────────────────────────────────


def test_prompt_crud_versioning(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore) -> tuple[int, int, int]:
        pid = await core.register_project(proj, "p")

        v1 = await core.create_prompt(pid, "deploy", "body v1")
        assert v1.version == 1 and v1.body == "body v1"

        # duplicate (project_id, name) → ValueError
        with pytest.raises(ValueError, match="already exists"):
            await core.create_prompt(pid, "deploy", "dup")

        # project-scoped create with an unknown project → ValueError
        with pytest.raises(ValueError, match="unknown project_id"):
            await core.create_prompt(9999, "x", "y")

        # update bumps version, leaves v1 intact (snapshot)
        v2 = await core.update_prompt(v1.id, "body v2")
        assert v2.version == 2 and v2.body == "body v2"
        assert v2.id != v1.id

        with pytest.raises(ValueError, match="unknown prompt_id"):
            await core.update_prompt(9999, "z")

        # get_prompt returns the specific version asked for
        got_v1 = await core.get_prompt(v1.id)
        assert got_v1 is not None and got_v1.body == "body v1"
        assert await core.get_prompt(9999) is None

        # list_prompts → only the latest version per name
        latest = await core.list_prompts(pid)
        assert [(p.name, p.version) for p in latest] == [("deploy", 2)]

        # a second, distinct prompt name (also project-scoped)
        await core.create_prompt(pid, "build", "build v1")
        latest2 = await core.list_prompts(pid)
        assert sorted((p.name, p.version) for p in latest2) == [
            ("build", 1),
            ("deploy", 2),
        ]
        # project filter excludes other projects
        assert await core.list_prompts(9999) == []

        # list_prompt_versions → [v1, v2] ordered asc
        versions = await core.list_prompt_versions(v1.id)
        assert [p.version for p in versions] == [1, 2]
        assert await core.list_prompt_versions(9999) == []

        return pid, v1.id, v2.id

    pid, v1_id, v2_id = _run(scenario, settings)

    # read-back via throwaway sync engine: both versions present, bodies
    # preserved (update is a snapshot, not an in-place rewrite).
    with _read(settings) as s:
        rows = list(
            s.scalars(
                select(Prompt)
                .where(Prompt.name == "deploy")
                .order_by(Prompt.version)
            )
        )
        assert [(r.version, r.body) for r in rows] == [
            (1, "body v1"),
            (2, "body v2"),
        ]


def test_prompt_delete_removes_all_versions(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def scenario(core: RelayCore) -> int:
        v1 = await core.create_prompt(None, "global", "g1")
        v2 = await core.update_prompt(v1.id, "g2")
        await core.update_prompt(v2.id, "g3")
        assert len(await core.list_prompt_versions(v1.id)) == 3

        assert await core.delete_prompt(v2.id) is True
        assert await core.list_prompt_versions(v1.id) == []
        assert await core.get_prompt(v1.id) is None
        assert await core.delete_prompt(v1.id) is False  # already gone
        return v1.id

    _run(scenario, settings)

    with _read(settings) as s:
        count = s.scalar(
            select(func.count())
            .select_from(Prompt)
            .where(Prompt.name == "global")
        )
        assert count == 0


def test_create_prompt_null_project_independent_namespace(
    tmp_path: Path,
) -> None:
    """A NULL-project prompt and a project-scoped prompt may share a name
    (UNIQUE is on (project_id, name, version); NULL project_id is its own
    namespace)."""
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(proj, "p")
        await core.create_prompt(None, "shared", "global body")
        await core.create_prompt(pid, "shared", "project body")
        # duplicate within the NULL namespace still rejected
        with pytest.raises(ValueError, match="already exists"):
            await core.create_prompt(None, "shared", "dup")

        glob = await core.list_prompts()  # no filter → all latest
        names = sorted(
            ((p.project_id, p.name) for p in glob),
            key=lambda t: (t[0] if t[0] is not None else -1, t[1]),
        )
        assert names == [(None, "shared"), (pid, "shared")]

    _run(scenario, settings)


# ── events / iters reads ───────────────────────────────────────────────


def test_list_events_windowing_and_list_iters(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(DONE_BLOCK)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        assert (await core.wait_for_run(run_id)).status == "done"

        all_ev = await core.list_events(run_id)
        seqs = [e.seq for e in all_ev]
        assert seqs == sorted(seqs)  # seq asc
        assert seqs == list(range(1, len(seqs) + 1))
        assert all_ev[0].kind == "run_started"
        assert all_ev[-1].kind == "run_ended"

        # after_seq strictly greater-than
        tail = await core.list_events(run_id, after_seq=2)
        assert [e.seq for e in tail] == seqs[2:]

        # offset + limit windowing (applied after after_seq filter)
        window = await core.list_events(run_id, limit=2, offset=1)
        assert [e.seq for e in window] == seqs[1:3]

        windowed_after = await core.list_events(
            run_id, after_seq=1, offset=1, limit=1
        )
        assert [e.seq for e in windowed_after] == [seqs[2]]

        # unknown run → empty
        assert await core.list_events("nope") == []

        iters = await core.list_iters(run_id)
        assert [i.seq for i in iters] == sorted(i.seq for i in iters)
        assert len(iters) == 1
        assert await core.list_iters("nope") == []
        return run_id

    _run(scenario, settings, harness)


# ── preview (PURE) ─────────────────────────────────────────────────────


def test_preview_run_pure_no_side_effects(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(proj, "p")

        out = await core.preview_run(pid, prompt_body="do the thing")
        assert set(out) == {"preamble", "body", "prompt", "run_dir"}
        assert out["body"] == "do the thing"
        assert out["prompt"] == out["preamble"] + "\n\n" + out["body"]
        assert out["run_dir"].endswith("/runs/<preview>")
        assert "RELAY_RUN_DIR: " in out["preamble"]
        assert "RELAY_PHASE:" not in out["preamble"]

        # phase carried into the preamble when given
        phased = await core.preview_run(
            pid, prompt_body="x", phase="planning"
        )
        assert "RELAY_PHASE: planning" in phased["preamble"]

        # prompt_id resolves the stored body
        stored = await core.create_prompt(pid, "saved", "stored body")
        by_id = await core.preview_run(pid, prompt_id=stored.id)
        assert by_id["body"] == "stored body"

        # unknown project
        with pytest.raises(ValueError, match="unknown project_id"):
            await core.preview_run(9999, prompt_body="x")
        # unknown prompt id
        with pytest.raises(ValueError, match="unknown prompt_id"):
            await core.preview_run(pid, prompt_id=12345)
        # neither prompt_body nor prompt_id
        with pytest.raises(ValueError, match="exactly one"):
            await core.preview_run(pid)
        # both prompt_body and prompt_id
        with pytest.raises(ValueError, match="exactly one"):
            await core.preview_run(
                pid, prompt_body="x", prompt_id=stored.id
            )

    _run(scenario, settings)

    # no runs row, no runs/ directory created by any preview call.
    with _read(settings) as s:
        assert s.scalar(select(func.count()).select_from(Run)) == 0
    assert not (settings.data_dir / "runs" / "<preview>").exists()
    runs_dir = settings.data_dir / "runs"
    assert not runs_dir.exists() or list(runs_dir.iterdir()) == []
