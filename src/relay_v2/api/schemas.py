"""Pydantic v2 request/response models for the REST API (spec.md §7).

Response models set ``from_attributes=True`` so they build straight from
ORM rows returned by :class:`RelayCore`. Request models carry only the
fields the corresponding ``RelayCore`` method accepts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── projects ───────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    root_path: str
    name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    root_path: str
    name: str
    created_at: datetime
    user_id: int


# ── prompts ────────────────────────────────────────────────────────────


class PromptCreate(BaseModel):
    project_id: int | None = None
    name: str
    body: str


class PromptUpdate(BaseModel):
    body: str


class PromptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    name: str
    version: int
    body: str
    created_at: datetime
    user_id: int


class PromptVersionsOut(BaseModel):
    versions: list[PromptOut]


# ── runs ───────────────────────────────────────────────────────────────


class RunCreate(BaseModel):
    project_id: int
    prompt_body: str | None = None
    prompt_id: int | None = None
    max_iters: int | None = None
    iter_timeout: int | None = None

    @model_validator(mode="after")
    def _exactly_one_prompt_source(self) -> RunCreate:
        if (self.prompt_body is None) == (self.prompt_id is None):
            raise ValueError(
                "exactly one of prompt_body / prompt_id must be provided"
            )
        return self


class RunResume(BaseModel):
    answer: str


class IterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    seq: int
    phase: str | None
    pi_session_id: str | None
    prompt: str
    preamble: str
    signal_kind: str | None
    signal_args: dict[str, Any] | None
    started_at: datetime
    ended_at: datetime | None
    exit_reason: str | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    prompt_id: int | None
    prompt_body: str
    user_id: int
    status: str
    started_at: datetime
    ended_at: datetime | None
    max_iters: int
    iter_timeout: int
    worktree_path: str | None
    branch: str | None
    parent_run_id: str | None


class RunDetailOut(RunOut):
    iters: list[IterOut] = Field(default_factory=list)


# ── events ─────────────────────────────────────────────────────────────


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str
    iter_id: int | None
    seq: int
    ts: datetime
    kind: str
    payload: dict[str, Any]


class PaginatedEventsOut(BaseModel):
    events: list[EventOut]
    after_seq: int
    limit: int
    offset: int


# ── preview ────────────────────────────────────────────────────────────


class PreviewOut(BaseModel):
    preamble: str
    body: str
    prompt: str
    run_dir: str
