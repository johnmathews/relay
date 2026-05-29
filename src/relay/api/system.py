"""Localhost-only filesystem browse endpoint for the project directory picker.

Read-only `GET /api/system/browse?path=...` that lists subdirectories of a
given path. Used by the register-project form's directory picker so the
user does not have to type an absolute path by hand.

There is no per-project sandbox here on purpose — the use case IS picking
*any* directory on the local filesystem to register as a project root.
Permitted by the single-user, localhost MVP envelope (ADR-12); not safe
to expose beyond `127.0.0.1`.

Also serves ``GET /api/system/defaults``: the run-option defaults the
dashboard's New Run wizard prefills (max_iters, iter_timeout). The
underlying values live on :class:`Settings`; this route just surfaces
them so the form fields can show concrete numbers instead of an opaque
"server default" placeholder.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from relay.api.schemas import SettingsDefaultsOut

router = APIRouter(prefix="/api/system", tags=["system"])


class DirEntry(BaseModel):
    name: str
    path: str


class BrowseOut(BaseModel):
    path: str
    parent: str | None
    entries: list[DirEntry]


@router.get("/defaults", response_model=SettingsDefaultsOut)
async def defaults(request: Request) -> SettingsDefaultsOut:
    """Return the run-option defaults the dashboard prefills.

    Surfaces ``Settings.max_iters`` and ``Settings.iter_timeout`` so the
    New Run wizard can render concrete numbers in the form fields rather
    than an opaque "server default" placeholder. The values are
    process-wide; an in-flight request reads the same instance as
    :class:`RelayCore` would inject into a run.
    """
    settings = request.app.state.core.settings
    return SettingsDefaultsOut(
        max_iters=settings.max_iters,
        iter_timeout=settings.iter_timeout,
    )


@router.get("/browse", response_model=BrowseOut)
async def browse(path: str = Query(default="~")) -> BrowseOut:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail="not a directory")
    parent: str | None = (
        str(resolved.parent) if resolved != resolved.parent else None
    )
    entries: list[DirEntry] = []
    try:
        for child in sorted(resolved.iterdir(), key=lambda x: x.name.lower()):
            try:
                if child.is_dir():
                    entries.append(DirEntry(name=child.name, path=str(child)))
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="permission denied") from None
    return BrowseOut(path=str(resolved), parent=parent, entries=entries)
