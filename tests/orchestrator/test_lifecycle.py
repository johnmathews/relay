"""W7: lifecycle-helper gaps — register_project idempotency, the
provision_workspace git-success branch (never exercised because every
loop test uses a non-git tmp dir), and compose_resume_prompt format."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from relay.config import Settings
from relay.db import init_db, make_async_engine, make_async_sessionmaker
from relay.orchestrator.lifecycle import (
    compose_resume_prompt,
    provision_workspace,
    register_project,
)


@asynccontextmanager
async def _sm(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield an async sessionmaker and dispose both engines. The sync
    `init_db` bootstrap engine is disposed immediately; the async engine
    in `finally` (in-loop) so the aiosqlite connection doesn't leak."""
    settings = Settings(data_dir=tmp_path / ".relay")
    init_db(settings).dispose()  # sync bootstrap engine — done after DDL
    engine = make_async_engine(settings.async_db_url)
    try:
        yield make_async_sessionmaker(engine)
    finally:
        await engine.dispose()


def test_register_project_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, int]:
        async with _sm(tmp_path) as sm:
            a = await register_project(sm, tmp_path, "proj")
            b = await register_project(sm, tmp_path, "proj-again")
            return a, b

    a, b = asyncio.run(scenario())
    assert a == b  # same row, not a duplicate


def test_provision_workspace_git_success(tmp_path: Path) -> None:
    """A real git work tree → a per-run worktree + branch are created
    (the success branch all loop tests skip via non-git tmp dirs).
    Worktree + run-dir land under ``<project_root>/.relay/`` per
    spec §3.3."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                    check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo,
                    check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"],
                    cwd=repo, check=True)

    async def scenario() -> tuple[Path | None, str | None, Path]:
        return await provision_workspace(repo, "run-xyz")

    wt, branch, run_dir = asyncio.run(scenario())
    assert wt is not None and wt.exists()
    assert wt.is_relative_to(repo / ".relay" / "worktrees")
    assert branch == "relay/run-xyz"
    assert run_dir == repo / ".relay" / "runs" / "run-xyz"


def test_provision_workspace_non_git_falls_back(tmp_path: Path) -> None:
    """Non-git root → no worktree/branch, run_dir still provisioned
    (the fallback every loop test relies on, now explicitly asserted)."""

    async def scenario() -> tuple[Path | None, str | None, Path]:
        return await provision_workspace(tmp_path, "run-1")

    wt, branch, run_dir = asyncio.run(scenario())
    assert wt is None and branch is None
    assert run_dir.exists()
    assert run_dir == tmp_path / ".relay" / "runs" / "run-1"


def test_compose_resume_prompt_format() -> None:
    out = compose_resume_prompt("Implement W3.", "A or B?", "Use A.")
    assert out.startswith("Implement W3.")
    assert "---" in out
    assert 'Answer to the paused question ("A or B?")' in out
    assert out.rstrip().endswith("Use A.")
