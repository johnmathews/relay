"""Production frontend serving (Phase 8, spec §11.2).

In production the built Vue SPA (``frontend/dist/``) is served by
FastAPI itself; in dev the Vite server proxies ``/api`` to the backend
and this mount is absent. The mount is **conditional and additive**:
when no built frontend is present (the entire test tree, and any source
checkout that has not run ``npm run build``) :func:`mount_frontend` is a
no-op, so the app's behaviour is byte-for-byte identical to pre-Phase-8.
No existing route changes; nothing here touches ``RelayCore`` or the
event store.

SPA fallback: vue-router runs in history mode, so an unknown non-asset
path (e.g. ``/projects/abc/runs/1``) must return ``index.html`` rather
than 404 — :class:`_SpaStaticFiles` does exactly that, and only that.
API/MCP/health/openapi paths never reach here because the mount is
appended **last** to the router (after the REST routers and the ``/mcp``
mount), and Starlette matches routes in order.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


def frontend_dist_dir() -> Path | None:
    """Locate the built frontend, or ``None`` if it has not been built.

    Mirrors :func:`relay_v2.harness.skills.bundled_skill_dir`'s
    resolution order: the packaged location (a wheel that force-included
    the build) is preferred; the repo-root ``frontend/dist`` is the
    fallback for the source/editable layout the Docker image uses. Unlike
    the skill resolver this returns ``None`` instead of raising — a
    missing build is the normal dev/test state, not an error.
    """
    pkg_root = Path(__file__).resolve().parent.parent  # …/relay_v2
    packaged = pkg_root / "frontend_dist"
    if (packaged / "index.html").is_file():
        return packaged
    # parents: [0]=api [1]=relay_v2 [2]=src [3]=<repo root>
    repo_root = Path(__file__).resolve().parents[3]
    source = repo_root / "frontend" / "dist"
    if (source / "index.html").is_file():
        return source
    return None


class _SpaStaticFiles(StaticFiles):
    """:class:`StaticFiles` that falls back to ``index.html`` on miss.

    A real asset request (``/assets/...``) that is missing still 404s —
    only a *non-asset* path (no file, no extension match) falls back, so
    a genuinely broken asset reference is not masked as the SPA shell.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and not Path(path).suffix:
                return await super().get_response("index.html", scope)
            raise


def mount_frontend(app: FastAPI) -> bool:
    """Mount the built SPA at ``/`` if it exists. Returns whether it did.

    Call this **after** every API router and the ``/mcp`` mount so the
    catch-all ``/`` is matched last (Starlette routes in registration
    order). No-op + ``False`` when the frontend has not been built.
    """
    dist = frontend_dist_dir()
    if dist is None:
        return False
    app.mount("/", _SpaStaticFiles(directory=dist, html=True), name="frontend")
    return True
