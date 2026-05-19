"""FastAPI application factory.

Surface: a ``/health`` route, the Phase 3 REST API (runs, projects,
prompts, file browser, SSE event stream — all thin adapters over the
single shared :class:`RelayCore`, ADR-07/ADR-15), and a lifespan that
materialises the schema and owns the orchestrator runtime (RelayCore
started/stopped with the app — ADR-07/ADR-19). The MCP server (Phase 5)
and OTel export (Phase 7) remain out of scope here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay_v2.api import include_api_routers
from relay_v2.config import Settings, get_settings
from relay_v2.core import RelayCore
from relay_v2.db import init_db
from relay_v2.harness import Harness
from relay_v2.version import __version__


def create_app(
    settings: Settings | None = None, *, harness: Harness | None = None
) -> FastAPI:
    """Build the FastAPI app. Pass explicit ``settings`` to isolate tests.

    ``harness`` is a test-isolation seam mirroring the existing
    ``settings`` injection: when given, it is passed straight through to
    ``RelayCore`` so tests can drive a scripted harness double instead of
    spawning real pi. Production callers leave it ``None`` (RelayCore
    defaults to :class:`PiHarness`). ADR-07: still one shared RelayCore.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = init_db(resolved)
        app.state.engine = engine
        app.state.settings = resolved
        core = RelayCore(resolved, harness=harness)
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

    include_api_routers(app)
    return app


# Module-level app for `uvicorn relay_v2.app:app` (used by `relay serve`).
app = create_app()
