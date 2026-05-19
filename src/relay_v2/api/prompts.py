"""Prompts router — versioned CRUD (spec.md §7). Adapter over RelayCore."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from relay_v2.api.deps import CoreDep, http_error
from relay_v2.api.schemas import (
    PromptCreate,
    PromptOut,
    PromptUpdate,
    PromptVersionsOut,
)

router = APIRouter(prefix="/api", tags=["prompts"])


@router.get("/prompts", response_model=list[PromptOut])
async def list_prompts(
    core: CoreDep, project_id: int | None = None
) -> list[PromptOut]:
    rows = await core.list_prompts(project_id)
    return [PromptOut.model_validate(r) for r in rows]


@router.post(
    "/prompts",
    response_model=PromptOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt(
    body: PromptCreate, core: CoreDep
) -> PromptOut:
    try:
        row = await core.create_prompt(body.project_id, body.name, body.body)
    except ValueError as exc:
        # unknown project_id → 404; duplicate (project_id, name) → 409.
        message = str(exc)
        status_code = 409 if "already exists" in message else 404
        raise HTTPException(status_code=status_code, detail=message) from exc
    return PromptOut.model_validate(row)


@router.get("/prompts/{prompt_id}", response_model=PromptOut)
async def get_prompt(
    prompt_id: int, core: CoreDep
) -> PromptOut:
    row = await core.get_prompt(prompt_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"unknown prompt_id={prompt_id}"
        )
    return PromptOut.model_validate(row)


@router.put("/prompts/{prompt_id}", response_model=PromptOut)
async def update_prompt(
    prompt_id: int,
    body: PromptUpdate,
    core: CoreDep,
) -> PromptOut:
    try:
        row = await core.update_prompt(prompt_id, body.body)
    except ValueError as exc:
        raise http_error(exc) from exc
    return PromptOut.model_validate(row)


@router.delete(
    "/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_prompt(
    prompt_id: int, core: CoreDep
) -> Response:
    if not await core.delete_prompt(prompt_id):
        raise HTTPException(
            status_code=404, detail=f"unknown prompt_id={prompt_id}"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/prompts/{prompt_id}/versions", response_model=PromptVersionsOut
)
async def list_prompt_versions(
    prompt_id: int, core: CoreDep
) -> PromptVersionsOut:
    rows = await core.list_prompt_versions(prompt_id)
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"unknown prompt_id={prompt_id}"
        )
    return PromptVersionsOut(
        versions=[PromptOut.model_validate(r) for r in rows]
    )
