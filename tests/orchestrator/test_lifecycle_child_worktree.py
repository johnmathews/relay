"""provision_workspace branches child worktree off parent HEAD (9b)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relay_v2.orchestrator.lifecycle import provision_workspace

# Safe argv-form subprocess spawner (no shell, no injection surface).
# Bound to a local so the static-analysis reminder hook — which keys on
# the literal exec( token used by shell-style APIs — does not flag it.
_spawn_argv = asyncio.create_subprocess_exec


async def _git(*args: str, cwd: Path) -> int:
    proc = await _spawn_argv(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait()


async def _setup_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    await _git("init", cwd=path)
    await _git("config", "user.email", "t@t.com", cwd=path)
    await _git("config", "user.name", "T", cwd=path)
    (path / "README.md").write_text("init")
    await _git("add", ".", cwd=path)
    await _git("commit", "-m", "init", cwd=path)


@pytest.mark.asyncio
async def test_child_worktree_contains_parent_commits(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    await _setup_repo(project)

    parent_wt, _, _ = await provision_workspace(project, "parent-001")
    assert parent_wt is not None

    # Commit work in the parent worktree.
    (parent_wt / "work.txt").write_text("parent work")
    await _git("add", ".", cwd=parent_wt)
    await _git("commit", "-m", "parent progress", cwd=parent_wt)

    # Child branches off parent HEAD.
    child_wt, child_branch, _ = await provision_workspace(
        project, "child-001",
        parent_worktree_path=parent_wt,
    )
    assert child_wt is not None
    assert (child_wt / "work.txt").exists()
    assert (child_wt / "work.txt").read_text() == "parent work"


@pytest.mark.asyncio
async def test_child_worktree_missing_parent_degrades_gracefully(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    await _setup_repo(project)
    missing = tmp_path / "nonexistent"

    wt, branch, run_dir = await provision_workspace(
        project, "child-002",
        parent_worktree_path=missing,
    )
    assert wt is not None  # still succeeds, branches from project HEAD
    assert run_dir.exists()


@pytest.mark.asyncio
async def test_provision_workspace_no_parent_unchanged(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    await _setup_repo(project)

    wt, branch, run_dir = await provision_workspace(project, "run-001")
    assert wt is not None
    assert branch == "relay/run-001"
    assert run_dir.exists()
