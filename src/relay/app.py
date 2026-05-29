"""FastAPI application factory.

Surface: a ``/health`` route, the Phase 3 REST API (runs, projects,
prompts, file browser, SSE event stream — all thin adapters over the
single shared :class:`RelayCore`, ADR-07/ADR-15), the Phase 5 MCP server
mounted at ``/mcp`` (same shared ``RelayCore``, ADR-27), and a lifespan
that materialises the schema and owns the orchestrator runtime
(RelayCore started/stopped with the app — ADR-07/ADR-19), and (Phase 8,
spec §11.2) the built Vue SPA served from ``frontend/dist/`` when
present. OTel export (Phase 7) remains out of scope here.

Static-mount ordering (Phase 8): the SPA catch-all is mounted at ``/``
**after** ``/mcp`` inside the lifespan, so it is the last route in
registration order and never shadows ``/health``, the REST routers, or
``/mcp``. It is a no-op when the frontend has not been built (the whole
test tree), so the app surface is unchanged in dev/test.

MCP wiring note (ADR-27, the #1367 footgun): a sub-app mounted via
``app.mount()`` does **not** get its ASGI lifespan auto-run by
Starlette, and ``streamable_http_app()``'s
``StreamableHTTPSessionManager`` is started in that lifespan. So the
host lifespan below explicitly enters ``mcp.session_manager.run()``
around its body — without it every ``/mcp`` request hangs. The MCP
server is built and mounted inside the lifespan (where the shared
``core`` exists); ``RelayCore.__init__`` constructs a DB engine eagerly,
so core must stay lazily created in the lifespan, not at import time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay.api import include_api_routers
from relay.api.static import mount_frontend
from relay.config import Settings, get_settings
from relay.core import RelayCore
from relay.db import init_db
from relay.harness import Harness
from relay.mcp import create_mcp_server
from relay.version import __version__


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
        # Build + mount the MCP server now that the shared core exists.
        # Mounting during lifespan startup is fine — Starlette matches
        # against ``app.router.routes`` per request, so appending the
        # Mount before ``yield`` makes ``/mcp`` routable for every
        # subsequent request. The sub-app's own ASGI lifespan is *not*
        # run by Starlette (ADR-27 #1367 footgun); we run its session
        # manager explicitly via the ``async with`` below.
        mcp = create_mcp_server(core)
        app.mount("/mcp", mcp.streamable_http_app())
        # Phase 8 (spec §11.2): serve the built SPA at "/" in
        # production. Appended last — after /health, the REST routers
        # and /mcp — so the catch-all never shadows an API path
        # (Starlette matches in registration order). No-op when the
        # frontend has not been built (dev/test), so the app surface is
        # unchanged there. Additive; touches no contract.
        mount_frontend(app)
        try:
            async with mcp.session_manager.run():
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


# Module-level app for `uvicorn relay.app:app` (used by `relay serve`).
app = create_app()
