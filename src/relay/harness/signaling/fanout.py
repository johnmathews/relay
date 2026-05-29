"""Pydantic models for the fanout sentinel payload (spec.md §5.1 / §12, 9b).

``FanoutPayload`` validates the JSON body between
``[[engteam:fanout-start]]`` and ``[[engteam:fanout-end]]``.
``FanoutParseError`` is raised when JSON fails to parse or the payload
fails validation; the orchestrator treats it identically to
:class:`~relay.harness.signaling.MarkerError`.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

__all__ = ["FanoutChild", "FanoutParseError", "FanoutPayload"]


class FanoutParseError(Exception):
    """Raised when the fanout JSON body fails to parse or validate."""


class FanoutChild(BaseModel):
    role: str
    prompt: str


# Hard cap on the number of children in a single fanout sentinel.
# Parser-enforced regardless of config; the per-deployment soft cap
# (settings.max_fanout_width, default 8) is enforced at dispatch.
MAX_FANOUT_CHILDREN_HARD_CAP = 32


class FanoutPayload(BaseModel):
    children: list[FanoutChild]
    join_prompt: str

    @field_validator("children")
    @classmethod
    def _children_bounds(cls, v: list[FanoutChild]) -> list[FanoutChild]:
        if not v:
            raise ValueError("fanout payload must list at least one child")
        if len(v) > MAX_FANOUT_CHILDREN_HARD_CAP:
            raise ValueError(
                f"fanout payload lists {len(v)} children; hard cap is "
                f"{MAX_FANOUT_CHILDREN_HARD_CAP}"
            )
        return v
