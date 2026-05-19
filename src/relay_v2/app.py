"""FastAPI application factory.

Surface so far: a ``/health`` route and a lifespan that materialises the
schema and owns the orchestrator runtime (the single shared
:class:`RelayCore`, started/stopped with the app — ADR-07/ADR-19). The
REST API, MCP server, and SSE feed are still out of scope here
(docs/plan.md Phases 3–5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay_v2.config import Settings, get_settings
from relay_v2.core import RelayCore
from relay_v2.db import init_db
from relay_v2.version import __version__


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. Pass explicit ``settings`` to isolate tests."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = init_db(resolved)
        app.state.engine = engine
        app.state.settings = resolved
        core = RelayCore(resolved)
        app.state.core = core
        await core.start()
        try:
            yield
        finally:
            await core.aclose()
            engine.dispose()

    app = FastAPI(title="relay", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


# Module-level app for `uvicorn relay_v2.app:app` (used by `relay serve`).
app = create_app()
