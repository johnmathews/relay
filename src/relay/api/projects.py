"""Projects router (spec.md §7). Thin adapter over RelayCore."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from relay.api.deps import CoreDep, http_error
from relay.api.schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/api", tags=["projects"])


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    core: CoreDep,
) -> list[ProjectOut]:
    rows = await core.list_projects()
    return [ProjectOut.model_validate(r) for r in rows]


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    body: ProjectCreate, core: CoreDep
) -> ProjectOut:
    try:
        project_id = await core.register_project(
            Path(body.root_path), body.name
        )
    except ValueError as exc:
        raise http_error(exc, default_status=400) from exc
    row = await core.get_project(project_id)
    if row is None:  # pragma: no cover - just-created row must exist
        raise HTTPException(status_code=500, detail="project vanished")
    return ProjectOut.model_validate(row)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int, core: CoreDep
) -> ProjectOut:
    row = await core.get_project(project_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"unknown project_id={project_id}"
        )
    return ProjectOut.model_validate(row)


@router.delete(
    "/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_project(
    project_id: int, core: CoreDep
) -> Response:
    """Unregister a project and cascade-delete its runs + prompts.
    DB-only; never touches files on disk. 404 unknown project; 409 if
    any run is currently active (cancel first)."""
    try:
        ok = await core.delete_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"unknown project_id={project_id}"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
