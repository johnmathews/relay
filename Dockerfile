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

# ── stage 2: build pi from fork (ADR-52) ───────────────────────────────
# The npm-published pi packages strip the @anthropic-ai/claude-agent-sdk
# bridge — without it PI_AGENT_SDK=1 silently falls back to the legacy
# direct-HTTP path that 400s with "out of extra usage" (verified on the
# LXC 2026-05-31, journal/260531-pi-bridge-fork-build.md). The bridge
# lives only in johnmathews/pi. Build from source to get a working
# subscription path.
#
# Bumping pi: re-cherry-pick onto a newer upstream tag (see Phase 1 of
# docs/plans/2026-05-31-pi-bridge-fork-rebuild.md), push as
# relay-bridge-vN, update PI_REF below + Settings.pi_expected_version +
# .tool-versions.
FROM node:22-slim AS pi
ARG PI_REPO=https://github.com/johnmathews/pi.git
ARG PI_REF=relay-bridge-v1
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/pi
RUN git init -q \
    && git remote add origin "${PI_REPO}" \
    && git fetch --depth 1 origin "${PI_REF}" \
    && git checkout -q FETCH_HEAD
# `npm ci` installs the lockfile — including @anthropic-ai/claude-agent-sdk
# and its platform-matched native `claude` binary (linux-x64 for amd64 LXCs).
RUN npm ci
# Sequential per-package build per the root package.json scripts.build.
RUN npm run build
# Drop dev deps + git history before the runtime stage copies us in.
RUN npm prune --omit=dev && rm -rf .git

# ── stage 3: python runtime ────────────────────────────────────────────
FROM python:3.13-slim AS runtime
# uv from the official distroless image (pinned major; uv is not a
# Python dep so uv.lock does not manage it).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

# Node 22 runtime + the built pi monorepo from the build stage.
# /opt/pi/packages/coding-agent/dist/cli.js is the entry; module
# resolution starts from its realpath and walks up to /opt/pi/
# node_modules/, which holds @anthropic-ai/claude-agent-sdk and its
# bundled native `claude` binary.
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /opt/pi /opt/pi
RUN ln -s /opt/pi/packages/coding-agent/dist/cli.js /usr/local/bin/pi

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
    && chown -R relay:relay /app /home/relay /opt/pi
USER relay

# Sanity-check pi is reachable as the relay user at build time. Fails
# the build if either the node binary or the binstub is misplaced —
# easier to catch here than at first run.
RUN pi --version

# Verify the agent-SDK bridge is actually shipped — guards against
# accidental reverts to npm-published pi which strips the bridge. The
# claude-agent-sdk JS lives in pi-ai's nested node_modules (workspace
# layout, not hoisted to root); the platform-specific native `claude`
# binary is installed by npm's optionalDependencies — at least one
# `linux-*` variant must be present (linux-x64 in CI/prod amd64,
# linux-arm64 on dev arm64).
RUN test -f /opt/pi/packages/ai/dist/providers/anthropic-agent-sdk.js \
    && test -d /opt/pi/packages/ai/node_modules/@anthropic-ai/claude-agent-sdk \
    && ls /opt/pi/node_modules/@anthropic-ai/ | grep -q '^claude-agent-sdk-linux-' \
    || (echo "FATAL: bridge artefacts missing — check Phase 1 of docs/plans/2026-05-31-pi-bridge-fork-rebuild.md" && exit 1)

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
