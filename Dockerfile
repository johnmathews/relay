# relay production image (Phase 8, spec §11.2).
#
# Two stages: (1) build the Vue SPA to frontend/dist/; (2) a uv-managed
# Python 3.13 runtime that serves both the REST/MCP API and the built
# SPA from one process. The repo layout is preserved inside the image
# (/app/frontend/dist) because relay.api.static.frontend_dist_dir()
# resolves the build relative to the source tree (parents[3]/frontend/
# dist) — no force-include needed for the source/`uv run` deployment.

# ── stage 1: frontend build ────────────────────────────────────────────
FROM node:22-slim AS frontend
WORKDIR /build/frontend
# Install deps from the lockfile first for layer caching.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # -> /build/frontend/dist

# ── stage 2: python runtime ────────────────────────────────────────────
FROM python:3.13-slim AS runtime
# uv from the official distroless image (pinned major; uv is not a
# Python dep so uv.lock does not manage it).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    RELAY_HOST=0.0.0.0 \
    RELAY_PORT=7800

WORKDIR /app

# Resolve deps from the lockfile before copying the source, so a code
# change does not bust the dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source + the bundled skill (force-included path matters
# for `relay install-skill`; the repo tree is what the editable layout
# resolves — both kept).
COPY src/ ./src/
COPY skills/ ./skills/
COPY README.md LICENSE .tool-versions ./
RUN uv sync --frozen --no-dev

# The built SPA, at the path frontend_dist_dir() looks for.
COPY --from=frontend /build/frontend/dist ./frontend/dist

# Drop privileges.
RUN useradd --create-home --uid 10001 relay \
    && chown -R relay:relay /app
USER relay

EXPOSE 7800

# Liveness without curl — python:slim has no curl, so the documented
# healthcheck-validation rule says use urllib instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:7800/health').status==200 else sys.exit(1)"]

# Run the installed console script straight from the venv (on PATH
# above). Not `uv run` — that would re-resolve and write the uv cache
# at container start; the environment is already fully materialised by
# the build-time `uv sync`.
CMD ["relay", "serve"]
