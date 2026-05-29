"""relay v2 REST API package (Phase 3).

Routers are thin adapters over the single shared :class:`RelayCore`
(ADR-07/ADR-15): handlers resolve ``request.app.state.core`` via the
shared :func:`relay.api.deps.get_core` dependency and never touch the
DB directly.

:func:`include_api_routers` is the one canonical place that mounts every
Phase 3 router (runs, projects, prompts, files, events) — called from
``create_app`` in production and directly from tests.
"""

from __future__ import annotations

from fastapi import FastAPI


def include_api_routers(app: FastAPI) -> None:
    """Mount every Phase 3 REST router on ``app``.

    Called from ``create_app`` in production; tests call it directly
    right after building the app. Imports are local so the package
    import stays cheap and load order can't surprise us.
    """
    from relay.api.artifacts import router as artifacts_router
    from relay.api.events import router as events_router
    from relay.api.files import router as files_router
    from relay.api.projects import router as projects_router
    from relay.api.prompts import router as prompts_router
    from relay.api.runs import router as runs_router
    from relay.api.system import router as system_router

    app.include_router(runs_router)
    app.include_router(projects_router)
    app.include_router(prompts_router)
    app.include_router(files_router)
    app.include_router(events_router)
    app.include_router(artifacts_router)
    app.include_router(system_router)


__all__ = ["include_api_routers"]
