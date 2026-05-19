"""Shared FastAPI dependencies + domain-error mapping.

All routers pull the single in-process :class:`RelayCore` off
``app.state.core`` (set by the lifespan in :mod:`relay_v2.app`). Route
handlers never construct a core or touch the DB directly (ADR-07/15).
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request

from relay_v2.core import RelayCore


def get_core(request: Request) -> RelayCore:
    """The process-wide RelayCore the lifespan put on app state."""
    # ``app.state`` is dynamically typed (Any); the lifespan guarantees
    # ``core`` is the single shared RelayCore (ADR-07).
    return cast(RelayCore, request.app.state.core)


# Reusable typed dependency — sidesteps ruff B008 (no Depends() call in a
# default arg) while keeping the canonical FastAPI injection pattern.
CoreDep = Annotated[RelayCore, Depends(get_core)]


# RelayCore signals every domain failure as ``ValueError`` (see core.py).
# The message text is the only discriminator, so map on substrings:
#   - state conflicts (resume on a non-paused / already-running run) → 409
#   - everything else → 404: unknown entity, "no saved pause prompt",
#     and resume of a run whose project was deleted ("... project N no
#     longer exists" — the referenced resource is gone, so 404 is the
#     right contract; deliberately NOT added to _CONFLICT_MARKERS).
# Bad-request cases (preview: neither/both prompt args) are mapped at the
# call site with ``http_error(exc, default_status=400)`` since the same
# message class ("must be provided") is unambiguously a 400 there.
_CONFLICT_MARKERS = ("is already running", "is not paused")


def http_error(exc: ValueError, *, default_status: int = 404) -> HTTPException:
    """Translate a RelayCore ``ValueError`` into an ``HTTPException``."""
    message = str(exc)
    if any(marker in message for marker in _CONFLICT_MARKERS):
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=default_status, detail=message)
