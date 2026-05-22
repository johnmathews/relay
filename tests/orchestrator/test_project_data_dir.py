"""Bug 1 regression: per-project workspace layout (spec.md §3.3).

Worktrees and run-artifacts dirs must live under the **registered
project's** `.relay/` directory, not under whatever cwd `relay serve`
happened to be launched from. The 9f live acceptance run surfaced
this: a project registered at /tmp/scratch had its run's worktree
provisioned at /relay-v2/.relay/worktrees/<run_id>, because the
orchestrator was passing `settings.data_dir` (the global SQLite root)
to `provision_workspace` instead of the project's own root.

These tests assert the new invariant: workspace location is derived
from `project.root_path`, independent of `settings.data_dir`.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from tests.orchestrator.scripted_harness import (
    ScriptedHarness,
    TextScript,
)

DONE_BLOCK = "All work complete.\n\n[[engteam:done]]"


def _settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir)


def _git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=repo, check=True,
    )


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


def test_worktree_under_project_root_not_global_data_dir(
    tmp_path: Path,
) -> None:
    """The bug: project registered at A, run started against it, but the
    worktree was created under settings.data_dir (somewhere else
    entirely) instead of under A/.relay/."""
    # Project root and the global data dir are *distinct* locations —
    # mirrors the live repro (project at /…/relay-fanout-test, data_dir
    # at /…/relay-v2/.relay/).
    project_root = tmp_path / "scratch-project"
    _git_init(project_root)
    global_data_dir = tmp_path / "global-relay-data"
    settings = _settings(global_data_dir)

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(project_root, "scratch")
        run_id = await core.start_run(pid, "Go.")
        assert (await core.wait_for_run(run_id)).status == "done"
        run = await core.get_run(run_id)
        assert run is not None and run.worktree_path is not None
        return run.worktree_path

    worktree_path = _run(scenario, settings)

    # Worktree lives under the *project's* .relay dir.
    expected_prefix = project_root / ".relay" / "worktrees"
    assert Path(worktree_path).is_relative_to(expected_prefix), (
        f"worktree {worktree_path} not under {expected_prefix}"
    )
    # And NOT under the global data dir.
    assert not Path(worktree_path).is_relative_to(global_data_dir), (
        f"worktree {worktree_path} leaked into global data_dir {global_data_dir}"
    )


def test_run_artifacts_dir_under_project_root(tmp_path: Path) -> None:
    """The artifacts dir (RELAY_RUN_DIR) is a sibling of worktrees per
    spec §3.3 — it must live under the project root too."""
    project_root = tmp_path / "scratch-project"
    project_root.mkdir()  # non-git is fine; falls back gracefully
    global_data_dir = tmp_path / "global-relay-data"
    settings = _settings(global_data_dir)

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(project_root, "scratch")
        run_id = await core.start_run(pid, "Go.")
        assert (await core.wait_for_run(run_id)).status == "done"
        artifacts = await core.get_run_artifacts_dir(run_id)
        assert artifacts is not None
        return str(artifacts)

    artifacts_path = _run(scenario, settings)
    expected_prefix = project_root / ".relay" / "runs"
    assert Path(artifacts_path).is_relative_to(expected_prefix)
    assert not Path(artifacts_path).is_relative_to(global_data_dir)
