"""Run-artifacts file browser (ADR-25) — a second sandboxed root.

spec §9.1's Artifacts pane browses a run's ``<data_dir>/runs/<run_id>/``
directory (the agent's ``improvement-plan.md``, ``evaluation-report.md``,
``discussions/`` …). That directory is a *sibling of the worktree*
(spec §3.3), deliberately outside any project ``root_path``, so the
Phase 3 project file browser cannot reach it.

This router exposes it read-only, scoped per run. The sandbox root is
derived **server-side** from the path's ``run_id`` segment
(``settings.data_dir / "runs" / <run_id>``) — never client-supplied —
and the *same* audited :func:`relay_v2.api.files.resolve_within_sandbox`
plus the shared :func:`~relay_v2.api.files.serve_listing` /
:func:`~relay_v2.api.files.serve_file` implementation are reused. One
audited confinement function, one serving path, two trust roots.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from relay_v2.api.deps import get_core
from relay_v2.api.files import serve_file, serve_listing
from relay_v2.config import Settings

router = APIRouter(prefix="/api", tags=["artifacts"])


def _err(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


async def _artifacts_root(request: Request, run_id: str) -> Path:
    """Resolve the run's artifacts dir, or raise via a JSON 404.

    Returns the sandbox root path. Raises :class:`_NotFound` carrying a
    ready JSON response when the run is unknown or its artifacts dir was
    never created — both are 404 (distinct details)."""
    core = get_core(request)
    run = await core.get_run(run_id)
    if run is None:
        raise _NotFound(_err(404, f"unknown run {run_id}"))
    settings = cast(Settings, request.app.state.settings)
    root = settings.data_dir / "runs" / run_id
    if not root.exists():
        raise _NotFound(_err(404, f"no artifacts for run {run_id}"))
    return root


class _NotFound(Exception):
    """Internal control-flow: carries a prepared JSON 404 response."""

    def __init__(self, response: JSONResponse) -> None:
        self.response = response


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    request: Request,
    path: str = Query(default=""),
) -> JSONResponse:
    """Directory listing of the run's artifacts dir (thin adapter over
    the shared :func:`~relay_v2.api.files.serve_listing`)."""
    try:
        root = await _artifacts_root(request, run_id)
    except _NotFound as nf:
        return nf.response
    return serve_listing(root, path)


@router.get("/runs/{run_id}/artifacts/{file_path:path}")
async def get_artifact(
    run_id: str,
    file_path: str,
    request: Request,
) -> JSONResponse:
    """Text content of one artifact file (thin adapter over the shared
    :func:`~relay_v2.api.files.serve_file`; binary → 415, >5 MiB →
    413)."""
    try:
        root = await _artifacts_root(request, run_id)
    except _NotFound as nf:
        return nf.response
    return serve_file(root, file_path)
