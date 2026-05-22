"""Run-artifacts file browser (ADR-25) — a second sandboxed root.

spec §9.1's Artifacts pane browses a run's
``<project_root>/.relay/runs/<run_id>/`` directory (the agent's
``improvement-plan.md``, ``evaluation-report.md``, ``discussions/`` …).
That directory is a *sibling of the worktree* (spec §3.3), under the
**project's** data dir — not the relay-global one.

This router exposes it read-only, scoped per run. The sandbox root is
derived **server-side** from the run row (``run_id`` → project →
``<project_root>/.relay/runs/<run_id>``) via
:meth:`RelayCore.get_run_artifacts_dir` — never client-supplied — and
the *same* audited :func:`relay_v2.api.files.resolve_within_sandbox`
plus the shared :func:`~relay_v2.api.files.serve_listing` /
:func:`~relay_v2.api.files.serve_file` implementation are reused. One
audited confinement function, one serving path, two trust roots.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from relay_v2.api.deps import get_core
from relay_v2.api.files import SandboxViolation, serve_file, serve_listing
from relay_v2.core import PauseReviewError

router = APIRouter(prefix="/api", tags=["artifacts"])


def _err(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


async def _artifacts_root(request: Request, run_id: str) -> Path:
    """Resolve the run's artifacts dir, or raise via a JSON 404.

    Returns the sandbox root path. Raises :class:`_NotFound` carrying a
    ready JSON response when the run is unknown or its artifacts dir was
    never created — both are 404 (distinct details)."""
    core = get_core(request)
    root = await core.get_run_artifacts_dir(run_id)
    if root is None:
        raise _NotFound(_err(404, f"unknown run {run_id}"))
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


@router.put("/runs/{run_id}/artifacts/{file_path:path}")
async def put_artifact(
    run_id: str,
    file_path: str,
    request: Request,
) -> JSONResponse:
    """Write text content to a sandboxed artifact during a paused review
    (spec §6.2, §7; ADR-40). Thin adapter over
    :meth:`relay_v2.core.RelayCore.write_artifact`.

    Body: ``{"content": str, "editor"?: str}``. The endpoint is the
    **single write entry point** on the run artifacts dir; it requires
    the run to be ``paused`` AND the requested path to equal the latest
    paused iter's ``signal_args.review_path`` (set by 14b). On success
    the event store gains one ``artifact_edited`` event with the
    pre/post SHA-256 hashes and sizes — the audit trail per ADR-10.

    Status mapping:

    - 200 — write succeeded; body is ``{path, size, sha256}``.
    - 400 — sandbox violation (absolute, ``..``, NUL in path, symlink
      escape).
    - 404 — unknown run.
    - 409 — run not paused / no review_path / path mismatch /
      missing intermediate directory.
    - 413 — body exceeds ``MAX_FILE_BYTES``.
    - 415 — body is not valid JSON, ``content`` is not a string, the
      ``editor`` field is not a string, or the content carries a NUL
      byte (binary).
    """
    core = get_core(request)
    try:
        body = await request.json()
    except Exception:
        return _err(415, "request body must be application/json")
    if not isinstance(body, dict):
        return _err(415, "request body must be a JSON object")
    content = body.get("content")
    if not isinstance(content, str):
        return _err(415, "body.content must be a UTF-8 string")
    editor = body.get("editor", "dashboard")
    if not isinstance(editor, str):
        return _err(415, "body.editor must be a string")

    try:
        result = await core.write_artifact(
            run_id, file_path, content, editor=editor
        )
    except SandboxViolation as exc:
        return _err(400, str(exc))
    except PauseReviewError as exc:
        if exc.code == "unknown_run":
            return _err(404, exc.detail)
        if exc.code == "too_large":
            return _err(413, exc.detail)
        if exc.code == "binary":
            return _err(415, exc.detail)
        # not_paused / no_review_path / path_mismatch / missing_parent_dir
        return _err(409, exc.detail)
    return JSONResponse(status_code=200, content=result)
