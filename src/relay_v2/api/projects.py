"""Projects router (spec.md §7). Thin adapter over RelayCore."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from relay_v2.api.deps import CoreDep
from relay_v2.api.schemas import ProjectCreate, ProjectOut

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
    project_id = await core.register_project(Path(body.root_path), body.name)
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
    if not await core.delete_project(project_id):
        raise HTTPException(
            status_code=404, detail=f"unknown project_id={project_id}"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
