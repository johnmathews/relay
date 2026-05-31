# relay production image (Phase 8, spec §11.2; pi bundled per ADR-51).
#
# Three stages: (1) build the Vue SPA to frontend/dist/; (2) install the
# pinned pi harness via npm into a Node 22 image; (3) a uv-managed
# Python 3.13 runtime that copies in the Node 22 binary + pi's
# node_modules, serves the REST/MCP API and the built SPA from one
# process, and spawns pi as a child process per ADR-04.
#
# pi auth state (Max-subscription OAuth token at ~/.pi/agent/auth.json)
# is NOT baked in — it is per-user and must be bind-mounted at runtime
# (see docker-compose.example.yml).

# ── stage 1: frontend build ────────────────────────────────────────────
FROM node:22-slim AS frontend
WORKDIR /build/frontend
# Install deps from the lockfile first for layer caching.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # -> /build/frontend/dist

# ── stage 2: pi install ────────────────────────────────────────────────
# Pin matches .tool-versions and `Settings.pi_expected_version`. Bump
# both together when upgrading pi (ADR-16 / OQ-5: pi is pinned, not
# floating).
FROM node:22-slim AS pi
ARG PI_VERSION=0.74.0
RUN npm install -g "@earendil-works/pi-coding-agent@${PI_VERSION}"

# ── stage 3: python runtime ────────────────────────────────────────────
FROM python:3.13-slim AS runtime
# uv from the official distroless image (pinned major; uv is not a
# Python dep so uv.lock does not manage it).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

# Node 22 runtime + pi binary copied from the pi stage. We copy the
# node binary directly (debian's apt nodejs is too old for pi). The
# entire node_modules tree is copied because npm hoists pi's
# transitive deps (undici etc.) to the top of the global modules dir
# rather than nesting them under the @earendil-works scope.
#
# The `pi` binstub is recreated as a symlink rather than COPY'd from
# the pi stage: Docker COPY dereferences symlinks, which would land
# pi's cli.js as a flat file under /usr/local/bin/ — Node would then
# search for `undici` from /usr/local/bin/ instead of from the
# node_modules tree, breaking ESM module resolution.
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js /usr/local/bin/pi

# git is needed by orchestrator/lifecycle.py:provision_workspace (per-run
# git worktrees — ADR-13) and by pi's tool calls (the agent routinely
# runs git commands on behalf of the user). ca-certificates lets git
# talk to HTTPS remotes from inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    RELAY_HOST=0.0.0.0 \
    RELAY_PORT=7800 \
    HOME=/home/relay \
    PI_AGENT_SDK=1

WORKDIR /app

# Resolve deps from the lockfile before copying the source, so a code
# change does not bust the dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application source + the bundled engineering-team skill. The skill
# tree ships in the image so harness.skills.bundled_skill_dir() resolves
# at runtime; pi receives it via `--skill <path>` injection (ADR-44).
COPY src/ ./src/
COPY skills/ ./skills/
COPY README.md LICENSE .tool-versions ./
RUN uv sync --frozen --no-dev

# The built SPA, at the path frontend_dist_dir() looks for.
COPY --from=frontend /build/frontend/dist ./frontend/dist

# Drop privileges. The relay user owns /home/relay so a bind-mounted
# ~/.pi (auth volume) is accessible when the host directory is chowned
# to uid 10001, OR when compose overrides `user:` to the host uid.
RUN useradd --create-home --uid 10001 relay \
    && chown -R relay:relay /app /home/relay
USER relay

# Sanity-check pi is reachable as the relay user at build time. Fails
# the build if either the node binary or the binstub is misplaced —
# easier to catch here than at first run.
RUN pi --version

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
