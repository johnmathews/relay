"""Runs router (spec.md §7). Thin adapter over RelayCore.

``GET /api/runs`` does status filtering + pagination in the handler:
``RelayCore.list_runs`` only takes ``project_id`` and there is no
service method for the rest. This is presentation-layer slicing of an
already-materialised list, not a write — acceptable per ADR-07/15.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from relay.api.deps import CoreDep, http_error
from relay.api.schemas import (
    EventOut,
    IterOut,
    PaginatedEventsOut,
    PreviewOut,
    RunCreate,
    RunDetailOut,
    RunOut,
    RunResume,
)

router = APIRouter(prefix="/api", tags=["runs"])


@router.post(
    "/runs", response_model=RunOut, status_code=http_status.HTTP_201_CREATED
)
async def create_run(
    body: RunCreate, core: CoreDep
) -> RunOut:
    # RunCreate's validator already enforced the mode-specific prompt rules.
    if body.mode == "chat":
        # Chat-mode runs start empty; resume_run (W2) feeds user messages
        # as subsequent iter bodies. No prompt_body / prompt_id to resolve.
        prompt_body = ""
    elif body.prompt_id is not None:
        prompt = await core.get_prompt(body.prompt_id)
        if prompt is None:
            raise HTTPException(
                status_code=404,
                detail=f"unknown prompt_id={body.prompt_id}",
            )
        prompt_body = prompt.body
    else:
        assert body.prompt_body is not None
        prompt_body = body.prompt_body
    try:
        run_id = await core.start_run(
            body.project_id,
            prompt_body,
            max_iters=body.max_iters,
            iter_timeout=body.iter_timeout,
            mode=body.mode,
        )
    except ValueError as exc:
        raise http_error(exc) from exc
    run = await core.get_run(run_id)
    if run is None:  # pragma: no cover - just-created run must exist
        raise HTTPException(status_code=500, detail="run vanished")
    return RunOut.model_validate(run)


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    core: CoreDep,
    project_id: int | None = None,
    status: str | None = None,
    mode: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_children: bool = False,
) -> list[RunOut]:
    rows = await core.list_runs(
        project_id, include_children=include_children, mode=mode
    )
    if status is not None:
        rows = [r for r in rows if r.status == status]
    rows = rows[offset : offset + limit]
    return [RunOut.model_validate(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: str, core: CoreDep
) -> RunDetailOut:
    run = await core.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    iters = await core.list_iters(run_id)
    detail = RunDetailOut.model_validate(run)
    detail.iters = [IterOut.model_validate(i) for i in iters]
    return detail


@router.get(
    "/runs/{run_id}/children",
    response_model=list[RunOut],
)
async def list_run_children(
    run_id: str, core: CoreDep
) -> list[RunOut]:
    """Direct children of a run (spec.md §7, 9e).

    Returns the rows where ``parent_run_id == run_id``, ordered by
    ``started_at`` ascending. Returns ``[]`` for a parent that never
    fanned out. 404 if ``run_id`` itself is unknown.
    """
    if await core.get_run(run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    children = await core.list_children(run_id)
    return [RunOut.model_validate(r) for r in children]


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: str, core: CoreDep
) -> RunOut:
    run = await core.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    await core.cancel_run(run_id)
    updated = await core.get_run(run_id)
    if updated is None:  # pragma: no cover - existed a line ago
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return RunOut.model_validate(updated)


@router.post("/runs/{run_id}/close", response_model=RunOut)
async def close_chat(
    run_id: str, core: CoreDep
) -> RunOut:
    """Close a chat-mode run (W3).

    Chat-only sibling of POST ``/runs/{id}/cancel``. Returns 404 if the
    run is unknown, 409 if it is not chat-mode, 409 if it is already
    terminal. Successful close flips ``status → closed`` and appends a
    ``run_ended`` event with ``summary="user closed chat"``.
    """
    run = await core.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    if run.mode != "chat":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} is not a chat-mode run "
                   f"(mode={run.mode!r})",
        )
    if run.status in ("done", "failed", "cancelled", "closed"):
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} is already terminal "
                   f"(status={run.status!r})",
        )
    try:
        await core.close_chat(run_id)
    except ValueError as exc:
        # Defensive: a status race between get_run and close_chat would
        # land here. Same mapping as resume — state conflict → 409, else 404.
        raise http_error(exc) from exc
    updated = await core.get_run(run_id)
    if updated is None:  # pragma: no cover - existed a line above
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return RunOut.model_validate(updated)


@router.post("/runs/{run_id}/reopen", response_model=RunOut)
async def reopen_run(run_id: str, core: CoreDep) -> RunOut:
    """Reopen a failed+no-signal run as paused (WU5 — ADR-53).

    Returns 404 if the run is unknown, 409 if the run is not failed,
    409 if the last iter's exit_reason is not a no-signal variant.
    On success, status flips to ``paused``, ``ended_at`` is cleared,
    and a ``pause_requested`` event is appended with a recovery question.
    """
    if await core.get_run(run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    try:
        await core.reopen_failed_as_paused(run_id)
    except ValueError as exc:
        raise http_error(exc) from exc
    updated = await core.get_run(run_id)
    if updated is None:  # pragma: no cover
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    return RunOut.model_validate(updated)


@router.post("/runs/{run_id}/resume", response_model=RunOut)
async def resume_run(
    run_id: str,
    body: RunResume,
    core: CoreDep,
) -> RunOut:
    # core.resume_run conflates "unknown run" and "exists but not
    # paused" into one ValueError ("run X is not paused"). The spec
    # wants unknown → 404 and a state conflict → 409, so disambiguate
    # by existence here before delegating (read-only pre-check; the
    # write still flows through core — ADR-07/15).
    if await core.get_run(run_id) is None:
        raise HTTPException(
            status_code=404, detail=f"unknown run {run_id}"
        )
    try:
        await core.resume_run(run_id, body.answer)
    except ValueError as exc:
        # "is not paused" / "is already running" → 409 (state conflict);
        # "no saved pause prompt" / deleted project → 404.
        raise http_error(exc) from exc
    updated = await core.get_run(run_id)
    if updated is None:  # pragma: no cover - resume validated existence
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return RunOut.model_validate(updated)


@router.get("/runs/{run_id}/events", response_model=PaginatedEventsOut)
async def list_run_events(
    run_id: str,
    core: CoreDep,
    after_seq: int = 0,
    limit: int = 100,
    offset: int = 0,
) -> PaginatedEventsOut:
    rows = await core.list_events(
        run_id, after_seq=after_seq, limit=limit, offset=offset
    )
    return PaginatedEventsOut(
        events=[EventOut.model_validate(e) for e in rows],
        after_seq=after_seq,
        limit=limit,
        offset=offset,
    )


@router.delete(
    "/runs/{run_id}", status_code=http_status.HTTP_204_NO_CONTENT
)
async def delete_run(run_id: str, core: CoreDep) -> None:
    """Delete a run and all of its events/iters/descendants.

    DB-only — does NOT touch files on disk (worktree, run_dir);
    mirrors ``DELETE /api/projects/{id}``. Reclaiming on-disk space is a
    separate manual ``rm -rf .relay/runs/<id>`` step.

    ``404`` if ``run_id`` is unknown. ``409`` if the run is currently
    active (``running``/``awaiting_children``) — cancel it first.
    """
    try:
        ok = await core.delete_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")


@router.get("/runs/{run_id}/preview", response_model=PreviewOut)
async def preview_run(
    run_id: str,
    core: CoreDep,
    prompt_body: str | None = None,
    prompt_id: int | None = None,
    phase: str | None = None,
) -> PreviewOut:
    """Pure render — no run row/dir/event is created (W1 contract).

    ``run_id`` here is the *project id* the caller wants to preview a
    run for (spec.md §7 nests preview under runs but it operates on a
    project + a prospective prompt). Non-int path segment → 400.
    """
    try:
        project_id = int(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"preview expects a numeric project id, got {run_id!r}",
        ) from exc
    try:
        rendered = await core.preview_run(
            project_id,
            prompt_body=prompt_body,
            prompt_id=prompt_id,
            phase=phase,
        )
    except ValueError as exc:
        # "exactly one of ... must be provided" → 400; unknown ids → 404.
        message = str(exc)
        default = 400 if "must be provided" in message else 404
        raise HTTPException(status_code=default, detail=message) from exc
    return PreviewOut(**rendered)
