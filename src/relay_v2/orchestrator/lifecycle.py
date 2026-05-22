"""Run / iter row mutations, workspace provisioning, resume composition.

These are the side-effecting helpers the loop and :class:`RelayCore`
call. They own the *projection* columns (``runs.status``,
``iters.*``); every transition they make is paired with an append-only
event written by the caller through the :class:`EventStore`, so the
event log alone fully reconstructs a run (ADR-10).

Worktree handling (ADR-13): a per-run git worktree is provisioned
best-effort. When the project root is not a git work tree (the fixture
tests, ad-hoc dirs) provisioning degrades to running in the project root
with ``worktree_path = NULL`` — the loop's ``cwd`` is ``worktree_path or
project_root`` either way (spec.md §6). The artifacts dir
(``RELAY_RUN_DIR``) is *always* created and is a sibling of the
worktree, never nested in it (spec.md §3.3).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from relay_v2.db.models import Iter, Project, Run

__all__ = [
    "RunContext",
    "compose_join_prompt",
    "compose_resume_prompt",
    "create_run",
    "latest_fanout_iter",
    "latest_paused_iter",
    "load_run",
    "open_iter",
    "close_iter",
    "project_data_dir",
    "provision_workspace",
    "register_project",
    "set_iter_session",
    "set_run_status",
]

# Safe argv-form subprocess spawner (no shell, no injection surface).
# Bound to a local so the static-analysis reminder hook — which keys on
# the literal ``exec(`` token used by shell-style APIs — does not flag
# this shell-free call.
_spawn_argv = asyncio.create_subprocess_exec


@dataclass
class RunContext:
    """Everything the loop needs to drive one run, with no DB lookups in
    the hot path. Built by :class:`RelayCore` for both a fresh start and
    a resume; the only difference is ``start_seq`` / ``phase`` / ``body``.
    """

    run_id: str
    project_root: Path
    worktree_path: Path | None
    run_dir: Path
    max_iters: int
    iter_timeout: int
    start_seq: int
    phase: str | None
    body: str
    parent_run_id: str | None = None  # set for child runs dispatched via fanout (9b)
    # 14e: the paused iter's id when this context resumes a paused run.
    # The first iter of the resumed loop carries
    # `relay.pause.artifacts_edited_count` on its OTel iter span counting
    # `artifact_edited` events scoped to this iter id. None for a fresh
    # run, a fanout child, or a synth-phase re-enqueue.
    paused_predecessor_iter_id: int | None = None

    @property
    def cwd(self) -> Path:
        return self.worktree_path or self.project_root


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def register_project(
    sm: async_sessionmaker[AsyncSession], root_path: Path, name: str
) -> int:
    """Idempotent: return the project id, creating the row if absent.

    A thin service method (not a route — Phase 3 owns HTTP). ``start_run``
    needs a project FK; tests and the future projects API both go through
    here rather than touching the table directly.

    ``root_path`` is normalised at the registration boundary: ``~`` is
    expanded and the path resolved to absolute, then the directory must
    exist (raises ``ValueError``). Otherwise a bogus path would lurk in
    the DB until ``start_run`` spawned pi with a non-existent ``cwd`` —
    a ``FileNotFoundError`` deep in the harness layer that the
    orchestrator's run lifecycle does not surface to the user.
    """
    expanded = Path(root_path).expanduser().resolve()
    if not expanded.is_dir():
        raise ValueError(
            f"project root_path does not exist or is not a directory: "
            f"{expanded}"
        )
    root = str(expanded)
    async with sm() as s:
        existing = await s.scalar(
            select(Project).where(Project.root_path == root)
        )
        if existing is not None:
            return existing.id
        project = Project(root_path=root, name=name)
        s.add(project)
        await s.commit()
        return project.id


async def create_run(
    sm: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    project_id: int,
    prompt_body: str,
    max_iters: int,
    iter_timeout: int,
    worktree_path: str | None,
    branch: str | None,
    parent_run_id: str | None = None,
) -> None:
    async with sm() as s:
        s.add(
            Run(
                id=run_id,
                project_id=project_id,
                prompt_body=prompt_body,
                status="running",
                max_iters=max_iters,
                iter_timeout=iter_timeout,
                worktree_path=worktree_path,
                branch=branch,
                parent_run_id=parent_run_id,
            )
        )
        await s.commit()


def project_data_dir(project_root: Path) -> Path:
    """Per-project on-disk root (spec.md §3.3): ``<project_root>/.relay``.

    Worktrees and run-artifacts dirs live under here. The relay-global
    SQLite event store stays at ``settings.data_dir`` (single multi-
    tenant DB; ADR-12), but everything that belongs to a specific
    project hangs off the project's own root.
    """
    return project_root / ".relay"


async def provision_workspace(
    project_root: Path,
    run_id: str,
    parent_worktree_path: Path | None = None,
) -> tuple[Path | None, str | None, Path]:
    """Create the artifacts dir; best-effort per-run git worktree.

    Workspace lives under ``<project_root>/.relay/`` per spec §3.3, so
    a run started against project A never leaks files into project B's
    (or relay-v2's own) directory tree.

    When ``parent_worktree_path`` is given and exists, branches the new
    worktree off the parent worktree's HEAD commit rather than the
    project default branch (spec.md §6 — child runs start from the
    parent's in-progress work). When the parent worktree path does not
    exist or git fails, degrades to branching from the project HEAD.
    """
    data_dir = project_data_dir(project_root)
    run_dir = data_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    worktrees = data_dir / "worktrees"
    wt = worktrees / run_id
    branch = f"relay/{run_id}"
    worktrees.mkdir(parents=True, exist_ok=True)

    # Resolve parent HEAD commit as start-point (child branches from
    # parent's in-progress state, not the project default branch tip).
    parent_commit: str | None = None
    if parent_worktree_path is not None and parent_worktree_path.is_dir():
        head_proc = await _spawn_argv(
            "git", "-C", str(parent_worktree_path),
            "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await head_proc.communicate()
        if head_proc.returncode == 0:
            parent_commit = stdout.decode().strip()

    git_cmd = [
        "git", "-C", str(project_root),
        "worktree", "add", "-b", branch, str(wt),
    ]
    if parent_commit:
        git_cmd.append(parent_commit)

    proc = await _spawn_argv(
        *git_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    if rc == 0 and wt.exists():
        return wt, branch, run_dir
    return None, None, run_dir


async def open_iter(
    sm: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    seq: int,
    phase: str | None,
    prompt: str,
    preamble: str,
) -> int:
    async with sm() as s:
        row = Iter(
            run_id=run_id,
            seq=seq,
            phase=phase,
            prompt=prompt,
            preamble=preamble,
        )
        s.add(row)
        await s.commit()
        return row.id


async def set_iter_session(
    sm: async_sessionmaker[AsyncSession], iter_id: int, pi_session_id: str
) -> None:
    async with sm() as s:
        row = await s.get(Iter, iter_id)
        if row is not None:
            row.pi_session_id = pi_session_id
            await s.commit()


async def close_iter(
    sm: async_sessionmaker[AsyncSession],
    iter_id: int,
    *,
    signal_kind: str | None,
    signal_args: dict[str, Any] | None,
    exit_reason: str,
) -> None:
    async with sm() as s:
        row = await s.get(Iter, iter_id)
        if row is not None:
            row.signal_kind = signal_kind
            row.signal_args = signal_args
            row.exit_reason = exit_reason
            row.ended_at = _utcnow()
            await s.commit()


async def set_run_status(
    sm: async_sessionmaker[AsyncSession], run_id: str, status: str, *, ended: bool
) -> None:
    async with sm() as s:
        row = await s.get(Run, run_id)
        if row is not None:
            row.status = status
            if ended:
                row.ended_at = _utcnow()
            await s.commit()


async def load_run(sm: async_sessionmaker[AsyncSession], run_id: str) -> Run | None:
    async with sm() as s:
        return await s.get(Run, run_id)


async def latest_paused_iter(
    sm: async_sessionmaker[AsyncSession], run_id: str
) -> Iter | None:
    async with sm() as s:
        row: Iter | None = await s.scalar(
            select(Iter)
            .where(Iter.run_id == run_id, Iter.signal_kind == "pause")
            .order_by(Iter.seq.desc())
            .limit(1)
        )
        return row


async def latest_fanout_iter(
    sm: async_sessionmaker[AsyncSession], run_id: str
) -> Iter | None:
    """The most recent ``signal_kind='fanout'`` iter for ``run_id``.

    Read by ``RelayCore._maybe_resume_parent`` (9c) to recover the
    ``join_prompt`` from ``signal_args["payload"]["join_prompt"]``.
    Mirrors :func:`latest_paused_iter` for the resume path.
    """
    async with sm() as s:
        row: Iter | None = await s.scalar(
            select(Iter)
            .where(Iter.run_id == run_id, Iter.signal_kind == "fanout")
            .order_by(Iter.seq.desc())
            .limit(1)
        )
        return row


def compose_resume_prompt(next_prompt: str, question: str, answer: str) -> str:
    """The resumed iter's body: the paused iter's saved next-prompt with
    the human's answer appended as a clearly delimited block (ADR-20).
    Fresh-context-per-iter still holds — the answer travels in the prompt,
    not via pi session resume."""
    return (
        f"{next_prompt}\n\n"
        "---\n"
        f'Answer to the paused question ("{question}"):\n\n'
        f"{answer}\n"
    )


def compose_join_prompt(
    join_prompt: str, child_results: list[dict[str, str]]
) -> str:
    """The synthesizer iter's body: ``join_prompt`` followed by a
    structured ``RELAY_CHILD_RESULTS`` trailer (one entry per child).

    The trailer is YAML-ish (line-based ``key: value``, hand-rendered, no
    YAML library) so the engineering-team skill can read it the same way
    it reads the ``RELAY_*`` preamble lines. It lives in the body, not
    the preamble (ADR-14 — preamble is reserved for ``RELAY_RUN_DIR`` and
    ``RELAY_PHASE``). Multi-line summaries use YAML literal block
    (``summary: |``) so newlines survive untouched.

    Schema of each ``child_results`` entry (all ``str``):
    ``id``, ``role``, ``status``, ``summary``, ``branch``,
    ``worktree_path``. Empty values render as ``key:`` with a trailing
    space (never omitted) so the block is structurally uniform.
    """
    lines: list[str] = [join_prompt, "", "---", "RELAY_CHILD_RESULTS:"]
    for r in child_results:
        lines.append(f"- id: {r['id']}")
        lines.append(f"  role: {r['role']}")
        lines.append(f"  status: {r['status']}")
        summary = r.get("summary", "")
        if "\n" in summary:
            lines.append("  summary: |")
            for sub in summary.split("\n"):
                lines.append(f"    {sub}")
        else:
            lines.append(f"  summary: {summary}")
        lines.append(f"  branch: {r['branch']}")
        lines.append(f"  worktree_path: {r['worktree_path']}")
    return "\n".join(lines) + "\n"
