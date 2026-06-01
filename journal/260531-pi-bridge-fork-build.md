# 2026-05-31 — Pi bridge fork-build (ADR-52)

## What

Deployed relay container on the agent LXC was billing pi calls against extra usage instead of the Max subscription, despite ADR-51 having shipped earlier the same day with `PI_AGENT_SDK=1` set in the image ENV and a working `~/.pi` bind-mount.

## Root cause

The bridge code that makes `PI_AGENT_SDK=1` route to Max — `packages/ai/src/providers/anthropic-agent-sdk.ts` and its dynamic `import("@anthropic-ai/claude-agent-sdk")` — does not exist in the npm-published `@earendil-works/pi-ai@0.74.0` package. Verified by downloading the tarball directly from `https://registry.npmjs.org/@earendil-works/pi-ai/-/pi-ai-0.74.0.tgz` and grepping: no `PI_AGENT_SDK` references, no `claude-agent-sdk` strings, no `anthropic-agent-sdk.js` file. Same story for `pi-ai@0.78.0` (latest at time of writing).

The bridge lives only in `johnmathews/pi`, implemented across W1→W14 + post-W14 fixes (~14 functional cherry-picked commits). Upstream `badlogic/pi-mono` has no concept of the bridge at any version.

## Decision

Build pi from the fork at a clean cherry-pick tag (`relay-bridge-v1`) rather than `npm install`ing the published package. The fork branch is a clean cherry-pick of bridge-essential commits onto fresh upstream v0.78.0 — no docs/journal/eng-team noise — plus a structural-type fix to avoid an undici-types `Response`-shadowing collision in `pi-agent-core` — pushed as an annotated tag so the Dockerfile can pin to an immutable ref.

## Outcome

ADR-52 records the decision; the Dockerfile fork-build stage replaces the npm install; a runtime-stage `RUN test -f …` assertion + `tests/test_dockerfile_invariants.py` guard against accidental reverts. Image build time grows by 1-3 minutes (60s on Apple Silicon local, expect 3-5 minutes cold-cache on CI amd64) and image size grew from ~400 MB to ~1.95 GB; both acceptable for a self-hosted single-tenant deployment.

## Surprising thing learned

The mechanism behind `PI_AGENT_SDK=1` is not a routing trick — it's that `claude-agent-sdk` spawns the bundled `claude` binary as a subprocess and Anthropic's backend identifies that subprocess as Max-eligible by its client signature. The OAuth token is the same; only the *client identity* differs. The pi commit message that documents this is `7844b1ba experiment(b.5): SDK package identity is the lever — official claude-agent-sdk routes to Max`.

Second surprise: an attempt to "rebase the bridge picks onto an older upstream base" (so the type fix wouldn't be needed) is fundamentally blocked by `npm`'s modern resolution of `@types/node` to 24.x. Every upstream pi tag from v0.74.0 onwards installs `undici-types@~7.16.0` today, which shadows the global `Response` type. The user's working local pi at SHA `7ad4e38` only builds because it was npm-installed back when caret resolution landed `@types/node@22.x`. There is no upstream base older than the type collision; the structural-type fix is the only way forward.

Third surprise (post-deploy): `claude-agent-sdk`'s native-binary variant selector (`sdk.mjs` `F5`) tries `linux-x64-musl` FIRST on Linux, and locks in whichever path `require.resolve` succeeds for. Both glibc and musl variants get installed by `npm ci` (no `libc` discriminator in their package.json `os`/`cpu` fields). On a glibc Debian image the musl ELF binary fails to find `/lib/ld-musl-x86_64.so.1`; Linux returns `ENOENT` for the missing interpreter; the SDK reports the misleading "Claude Code native binary not found at .../claude-agent-sdk-linux-x64-musl/claude" (the file IS at the path — it's the interpreter that's missing). Fix landed as a one-line `RUN rm -rf /opt/pi/node_modules/@anthropic-ai/claude-agent-sdk-*-musl` in the Dockerfile so the selector's fallback finds the glibc variant.

## Verified

- **2026-06-01**: live container smoke test on the LXC returned a real
  `turn_start` → thinking deltas → text `"OK"` → `agent_end`, with the
  agent-SDK-only `thinkingSignature` blob present. No extra-usage 400.
  Image SHA `51b89fe42ace...`.
- **2026-06-01**: `claude.ai/settings/usage` confirmed Max bar
  advancing on a chat-mode session via the deployed dashboard at
  `192.168.2.107:7800`; extra-usage bar flat.

## Followups landed in the wrap-up

- Plan moved from `docs/plans/` to `docs/archive/` with a closed
  lifecycle header pointing at the deployed image SHA and the
  hotfix commit. Inbound links in `docs/getting-started.md` and
  `docs/harness.md` updated; ADR-52's path reference left stale
  per the append-only convention (the file is still findable by
  name in archive).
- Memory entries written under `~/.claude/projects/.../memory/`
  capturing the load-bearing context for future sessions: the
  fork-build delivery, the musl trap, the npm-artefact-vs-source
  trap, the undici-types collision, and three feedback patterns
  validated during the work (investigate-before-decide, confirm
  before destructive pushes, amend corrupted commits in place).
