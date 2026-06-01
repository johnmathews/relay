# Pi-bridge fork rebuild + relay image switch — Implementation Plan

**Status:** closed 2026-06-01. **Last updated:** 2026-06-01. **Supersedes:** none.

All six phases executed; image `ghcr.io/johnmathews/relay:latest` (SHA `51b89fe42ace...`) deployed on the LXC, live OAuth smoke check returned a streamed response with the agent-SDK-only `thinkingSignature` and zero "out of extra usage" errors; `claude.ai/settings/usage` confirmed Max bar advancing on a chat-mode turn. Outcome recorded in ADR-52 (`../decisions.md`) and `../../journal/260531-pi-bridge-fork-build.md`. One post-deploy hotfix (`build(docker): drop claude-agent-sdk musl variants`, commit `48398ba`) addressed a third surprise discovered during the LXC smoke — see the journal's "Surprising thing learned" section for the variant-selector trap.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `PI_AGENT_SDK=1` actually route to the Claude Max subscription in the deployed relay container by (a) producing a clean `johnmathews/pi` branch that re-applies only the bridge-essential commits on top of fresh upstream `badlogic/pi-mono`, and (b) updating the relay Docker image to build pi from that branch instead of `npm install`ing the published package, which strips the bridge.

**Architecture:**
- **pi side:** new branch `relay-bridge` in `johnmathews/pi`, cherry-picked from upstream `main` + the W1→W14 bridge series + post-W14 fixes. Tagged `relay-bridge-vN` so the relay Dockerfile can pin to an immutable ref.
- **relay side:** Dockerfile stage 2 stops doing `npm install -g @earendil-works/pi-coding-agent@…` and instead clones the fork at the tagged SHA, runs `npm ci && npm run build && npm prune --omit=dev`, and the runtime stage copies the built monorepo to `/opt/pi/` with a `/usr/local/bin/pi` symlink. Everything below the harness contract is unchanged — pi still spawns as a child process per ADR-04.

**Tech Stack:** Docker multi-stage build, Node 22, npm workspaces, FastAPI/Python 3.13 (unchanged), GHCR for image publishing, Ansible-managed compose on the LXC (no template change needed since `relay_version: "latest"`).

**Out of scope:** ansible role changes, contributing the bridge upstream to `badlogic/pi-mono`, switching harnesses, the credential-proxy sidecar architecture.

---

## File structure (everything this plan touches)

### `johnmathews/pi` repo (fork)
- Create branch: `relay-bridge` (new, off upstream/main)
- Create tag: `relay-bridge-v1` (annotated, on the tip of the new branch)

### `johnmathews/relay` repo (this repo)
- Modify: `Dockerfile` — replace stage 2 (pi install) and the stage-3 copy block
- Modify: `.tool-versions` — bump pi version line to whatever the fork's `packages/coding-agent/package.json` reports
- Modify: `src/relay/config.py:55` — bump `pi_expected_version` to the same string
- Modify: `docker-compose.example.yml` — comment update (pi is now built from fork, mention which)
- Modify: `docs/harness.md` — update the "Invocation" / PI_AGENT_SDK mechanism paragraph
- Modify: `docs/getting-started.md` §8 (Docker) — note the fork-build provenance
- Modify: `docs/decisions.md` — append ADR-52 documenting the discovery + decision
- Modify: `README.md` — if it mentions pi version provenance, update
- Modify: `docs/acceptance-testing.md` — add a "subscription path verification" item
- Create: `tests/test_dockerfile_invariants.py` — pytest that asserts the Dockerfile contains the load-bearing pi-build invariants (fork URL, pinned SHA, claude-agent-sdk verification step)
- Create: `journal/260531-pi-bridge-fork-build.md` — dated journal entry per the CLAUDE.md global rule

---

## Phase 1 — Restructure the pi fork

This phase happens in a fresh clone of `johnmathews/pi` (NOT the existing `/Users/john/projects/pi/` working copy, to avoid disturbing in-progress work). The output is a pushed branch + tag that phase 2 will reference.

### Task 1.1: Set up a clean working tree for the rebuild

**Files:**
- Working dir: `/tmp/pi-bridge-rebuild/`

- [ ] **Step 1: Clone the fork fresh into a temp dir**

```bash
mkdir -p /tmp/pi-bridge-rebuild && cd /tmp/pi-bridge-rebuild
git clone https://github.com/johnmathews/pi.git .
git remote add upstream https://github.com/badlogic/pi-mono.git
git fetch upstream
git fetch origin
```

- [ ] **Step 2: Verify upstream main is fetched and identify its tip**

```bash
UPSTREAM_TIP=$(git rev-parse upstream/main)
echo "Upstream main tip: $UPSTREAM_TIP"
git log --oneline -1 upstream/main
```

Expected: a single commit line printed, recent date.

- [ ] **Step 3: Enumerate the fork's commits ahead of upstream/main**

```bash
git log --oneline upstream/main..origin/main > /tmp/pi-bridge-rebuild/fork-ahead.txt
wc -l /tmp/pi-bridge-rebuild/fork-ahead.txt
cat /tmp/pi-bridge-rebuild/fork-ahead.txt
```

Expected: ~40 lines. Keep this file for reference — the bridge-essential commits are picked from it in Task 1.3.

### Task 1.2: Classify commits into "essential" vs "drop"

**Files:**
- Create: `/tmp/pi-bridge-rebuild/cherry-pick-list.txt`

- [ ] **Step 1: Write the cherry-pick allowlist**

The rule: keep functional code commits in the W1→W14 bridge series + post-W14 fixes; drop journal/eng-team docs commits and the pre-W1 experiment commits (W13 already deletes the experiment dead code).

Write `/tmp/pi-bridge-rebuild/cherry-pick-list.txt` in commit order (oldest first):

```text
# Bridge feature series — W1 through W9
e70e3bc4  # W1 chat-only agent-SDK bridge for Max-quota routing
476be42d  # W2 TypeBox → MCP adapter
36fb021d  # W3 thread pi MCP server to Anthropic OAuth bridge
ee9686ae  # W4 consume pi MCP server in agent-SDK bridge
9b6265c8  # W5 rewrite history serialisation for tighter encoding
37d7c48c  # W6 isolate spawned claude subprocess env
a6bf1f84  # W7 namespace pi tool names in system-prompt append
1a6605e0  # W8 fire extension lifecycle hooks at MCP handler boundary
0c342953  # W9 emit synthetic before/after-provider events at bridge boundary

# Tests — W11
b4942483  # W11a mocked agent-SDK bridge regression
b5c68fba  # W11b real-network agent-SDK bridge smoke

# Cleanup + classifier fixes — W13/W14
a25cc24c  # W13 delete subkey-exchange dead code and Experiment B toggles
f2826724  # W14 keep pi's system prompt under Anthropic's classifier threshold

# Post-W14 fixes
22866117  # disable Claude Code built-in tool preset under PI_AGENT_SDK
d117b647  # suppress stale extra-usage warning under PI_AGENT_SDK=1
```

Deliberately skipped: experiment commits (`233f4edd`, `0df74291`, `5c2e53e5`, `31a31175`, `7844b1ba`) — these are research that W13 deletes; the eng-team docs commits (`be5b574f`, `17a80c97`, `7c741a8d`, `876b6c26`, `a78fc371`, `7420d0af`, `d2d5a86c`, `7ad4e383`, `876b6c26`, `bc976fab`, `a0a78cf1`, `7844b1ba`-investigation-docs, etc.); the model-registry regen (`307903db`) which we'll re-do post-cherry-pick if anything still references stale models.

- [ ] **Step 2: Verify every SHA in the allowlist exists**

```bash
while read sha _; do
    [ -z "$sha" ] || [ "${sha:0:1}" = "#" ] && continue
    git cat-file -e "$sha" 2>/dev/null || echo "MISSING: $sha"
done < /tmp/pi-bridge-rebuild/cherry-pick-list.txt
```

Expected: zero "MISSING" lines.

### Task 1.3: Cherry-pick onto fresh upstream main

**Files:**
- Branch: `relay-bridge` (new)

- [ ] **Step 1: Create the branch from upstream tip**

```bash
git checkout -b relay-bridge upstream/main
```

- [ ] **Step 2: Cherry-pick each commit in order**

```bash
grep -v '^#\|^$' /tmp/pi-bridge-rebuild/cherry-pick-list.txt | awk '{print $1}' | \
while read sha; do
    echo "=== picking $sha ==="
    git cherry-pick -x "$sha" || { echo "CONFLICT at $sha — pausing"; break; }
done
```

The `-x` flag adds a "cherry picked from commit <sha>" trailer so provenance is preserved.

- [ ] **Step 3: Resolve conflicts as they arise**

For each conflict pause:
1. `git status` to see conflicting files.
2. Open each conflicting file, manually resolve. Most likely conflict zones: `packages/ai/src/providers/anthropic.ts` (upstream may have moved or refactored sibling code), `packages/coding-agent/src/modes/interactive/interactive-mode.ts` (large file, upstream churn). Resolve to keep BOTH the bridge changes AND the upstream improvements.
3. `git add <files>` then `git cherry-pick --continue`.

If a commit becomes entirely empty after resolution (because upstream now contains the change), skip with `git cherry-pick --skip` and add a note to the cherry-pick log.

- [ ] **Step 4: Verify the bridge files are present at HEAD**

```bash
test -f packages/ai/src/providers/anthropic-agent-sdk.ts && echo "bridge present" || echo "BRIDGE MISSING"
test -f packages/ai/src/providers/agent-sdk-mcp-tools.ts && echo "mcp-tools present" || echo "MCP-TOOLS MISSING"
grep -q '@anthropic-ai/claude-agent-sdk' packages/ai/package.json && echo "dep declared" || echo "DEP MISSING"
grep -q 'piAgentSdkEnabled' packages/ai/src/providers/anthropic.ts && echo "gate present" || echo "GATE MISSING"
```

Expected: all four lines say "present" / "declared".

### Task 1.4: Verify the rebuilt fork builds + tests pass

**Files:**
- Working dir: `/tmp/pi-bridge-rebuild/`

- [ ] **Step 1: Install dependencies fresh**

```bash
rm -rf node_modules packages/*/node_modules
npm ci
```

Expected: completes without error in 1-3 min. If it fails (lockfile drift after upstream sync), regenerate with `npm install` and commit the lockfile update as a final cleanup commit on the branch.

- [ ] **Step 2: Build all packages**

```bash
npm run build
```

Expected: each of `packages/tui`, `ai`, `agent`, `coding-agent`, `web-ui` builds without TypeScript errors. If a build fails because of upstream API drift in a sibling package, fix it inline and amend the relevant cherry-picked commit (`git commit --amend` after the fix) or stack a fixup commit at the end.

- [ ] **Step 3: Verify the built bridge file exists in dist**

```bash
test -f packages/ai/dist/providers/anthropic-agent-sdk.js && echo "OK: bridge built" || echo "FAIL"
test -f packages/ai/dist/providers/agent-sdk-mcp-tools.js && echo "OK: mcp-tools built" || echo "FAIL"
```

- [ ] **Step 4: Run pi's own test suite**

```bash
./test.sh
```

Expected: all tests pass. If a test added in W11 (the bridge regression test) fails, that's a real regression — fix before continuing. If a test unrelated to the bridge fails because of upstream churn, decide whether to skip it on this branch (with a note in the cherry-pick log) or fix it.

- [ ] **Step 5: Run the laptop-equivalent smoke check**

```bash
PI_AGENT_SDK=1 ./packages/coding-agent/dist/cli.js -p "reply with the single word OK and nothing else" --mode json --provider anthropic --model claude-sonnet-4-5 2>&1 | head -3
```

Expected: first three lines should be `session`, `agent_start`, `turn_start` event types — NOT a `400 "out of extra usage"` error. This confirms the rebuilt branch routes through the subscription path locally.

### Task 1.5: Push the branch and tag

**Files:**
- `johnmathews/pi` remote

- [ ] **Step 1: Capture the version pi reports**

```bash
PI_VER=$(node -e "console.log(require('./packages/coding-agent/package.json').version)")
echo "Pi version on this branch: $PI_VER"
```

Note this — it goes into the relay-side `.tool-versions` and `Settings.pi_expected_version` updates in Phase 3.

- [ ] **Step 2: Tag the branch tip**

```bash
git tag -a "relay-bridge-v1" -m "Pi $PI_VER + agent-SDK bridge for relay's PI_AGENT_SDK=1 path"
```

- [ ] **Step 3: Push branch and tag to origin**

```bash
git push origin relay-bridge
git push origin relay-bridge-v1
```

- [ ] **Step 4: Record the tagged SHA**

```bash
BRIDGE_SHA=$(git rev-parse relay-bridge-v1^{commit})
echo "BRIDGE_SHA=$BRIDGE_SHA"
echo "BRIDGE_SHA=$BRIDGE_SHA" >> /tmp/pi-bridge-rebuild/handoff.env
```

This `BRIDGE_SHA` is the value that goes into the relay Dockerfile's `ARG PI_REF=…` in Task 2.1.

- [ ] **Step 5: Commit the handoff log to the working tree (NOT pushed)**

Save `/tmp/pi-bridge-rebuild/cherry-pick-list.txt` and any conflict notes alongside the journal entry created in Task 5.6. Phase 1 produces no commit in the relay repo.

---

## Phase 2 — Update the relay Dockerfile

### Task 2.1: Replace the pi install stage with a fork-build stage

**Files:**
- Modify: `Dockerfile:22-29`

- [ ] **Step 1: Write the failing test for the Dockerfile invariants**

Create `tests/test_dockerfile_invariants.py`:

```python
"""Guard against accidental reverts of the pi-from-fork build (ADR-52).

The Dockerfile stage that produces pi for the runtime image must:
1. Reference johnmathews/pi (not @earendil-works/pi-coding-agent on npm).
2. Pin to an immutable ref (SHA or annotated tag).
3. Run `npm ci && npm run build` so the bridge files are produced.
4. Result in /opt/pi being copied into the runtime image.

If any of these invariants is broken, the published image will silently
fall back to npm-published pi which strips the @anthropic-ai/
claude-agent-sdk bridge and bills against extra usage instead of the
Max subscription. The runtime symptom is a 400 from Anthropic, but it
only shows up under live OAuth — too late for CI.
"""
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_pi_stage_builds_from_fork() -> None:
    text = DOCKERFILE.read_text()
    assert "johnmathews/pi" in text, "pi must be cloned from the fork"
    assert "npm ci" in text and "npm run build" in text, (
        "pi must be built from source, not installed from npm"
    )
    assert "@earendil-works/pi-coding-agent" not in text, (
        "must not fall back to npm-published pi (strips agent-sdk bridge)"
    )


def test_pi_ref_is_immutable() -> None:
    text = DOCKERFILE.read_text()
    # Match ARG PI_REF=<value> — value must be a 40-char SHA or a tag
    # starting with "relay-bridge-" (our annotated tag convention).
    import re

    match = re.search(r"ARG\s+PI_REF=([^\s]+)", text)
    assert match, "Dockerfile must declare ARG PI_REF"
    ref = match.group(1)
    assert (
        len(ref) == 40 or ref.startswith("relay-bridge-")
    ), f"PI_REF must be a full SHA or a relay-bridge tag; got {ref!r}"


def test_runtime_stage_copies_built_tree() -> None:
    text = DOCKERFILE.read_text()
    assert "COPY --from=pi" in text, "runtime stage must copy from pi build stage"
    assert "/opt/pi" in text, (
        "runtime stage must place the built pi tree at /opt/pi "
        "(matches the symlink target)"
    )
```

- [ ] **Step 2: Run the test, expect failure**

```bash
uv run pytest tests/test_dockerfile_invariants.py -v
```

Expected: 3 failures (current Dockerfile uses `npm install` from npm, has no `johnmathews/pi`, no `/opt/pi`).

- [ ] **Step 3: Edit Dockerfile stage 2**

Replace lines 22-29 (`# ── stage 2: pi install ──` through the `RUN npm install -g …` line) with:

```dockerfile
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
```

- [ ] **Step 4: Edit Dockerfile stage 3 copy block (lines 36-49)**

Replace:

```dockerfile
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/@earendil-works/pi-coding-agent/dist/cli.js /usr/local/bin/pi
```

…with:

```dockerfile
# Node 22 runtime + the built pi monorepo from the build stage.
# /opt/pi/packages/coding-agent/dist/cli.js is the entry; module
# resolution starts from its realpath and walks up to /opt/pi/
# node_modules/, which holds @anthropic-ai/claude-agent-sdk and its
# bundled native `claude` binary.
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /opt/pi /opt/pi
RUN ln -s /opt/pi/packages/coding-agent/dist/cli.js /usr/local/bin/pi
```

- [ ] **Step 5: Extend the chown step so /opt/pi is owned by the relay user**

Find the line:

```dockerfile
    && chown -R relay:relay /app /home/relay
```

…and change to:

```dockerfile
    && chown -R relay:relay /app /home/relay /opt/pi
```

- [ ] **Step 6: Run the test again, expect pass**

```bash
uv run pytest tests/test_dockerfile_invariants.py -v
```

Expected: 3 passes.

- [ ] **Step 7: Add a runtime sanity check for the bridge artifacts**

Right after the existing `RUN pi --version` line, add:

```dockerfile
# Verify the agent-SDK bridge is actually shipped — guards against
# accidental reverts to npm-published pi which strips the bridge.
RUN test -f /opt/pi/packages/ai/dist/providers/anthropic-agent-sdk.js \
    && test -d /opt/pi/node_modules/@anthropic-ai/claude-agent-sdk \
    && ls /opt/pi/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64 > /dev/null \
    || (echo "FATAL: bridge artefacts missing — check Phase 1 of docs/plans/2026-05-31-pi-bridge-fork-rebuild.md" && exit 1)
```

Place it after `RUN pi --version` and before `EXPOSE 7800`.

- [ ] **Step 8: Build the image locally to validate**

```bash
docker build -t relay:fork-build-test .
```

Expected: build completes. The new pi stage adds ~1-3 minutes the first time; subsequent builds are layer-cached.

- [ ] **Step 9: Inspect the image for the bridge artifacts**

```bash
docker run --rm relay:fork-build-test sh -c '
    ls /opt/pi/packages/ai/dist/providers/anthropic-agent-sdk.js
    ls /opt/pi/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs
    ls /opt/pi/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64
    pi --version
'
```

Expected: each `ls` succeeds, `pi --version` reports the same version as the fork's `packages/coding-agent/package.json`.

- [ ] **Step 10: Commit**

```bash
git add Dockerfile tests/test_dockerfile_invariants.py
git commit -m "$(cat <<'EOF'
build(docker): build pi from johnmathews/pi:relay-bridge-v1 (ADR-52)

npm-published pi strips the @anthropic-ai/claude-agent-sdk bridge,
silently falling back to the direct-HTTP path that 400s with "out of
extra usage" instead of routing to the Max subscription. Build from
the fork at the relay-bridge-v1 tag so the bridge ships in the image.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Settings + version alignment

### Task 3.1: Update pi version pins to match the fork tag

**Files:**
- Modify: `.tool-versions`
- Modify: `src/relay/config.py:55`

- [ ] **Step 1: Read the fork's coding-agent version**

The pi version reported by the fork-built binary comes from `packages/coding-agent/package.json` in the fork. Substitute that value (let's call it `<PI_VER>`) for the placeholders below. Typically it'll still be `0.74.0` since the cherry-pick doesn't change the package.json `version` field.

- [ ] **Step 2: Update `.tool-versions`**

If the version unchanged (`0.74.0`), no edit needed. Otherwise replace the `pi 0.74.0` line with `pi <PI_VER>`.

- [ ] **Step 3: Update `Settings.pi_expected_version` in `src/relay/config.py`**

Locate line 55:

```python
    pi_expected_version: str = "0.74.0"
```

Replace `"0.74.0"` with `"<PI_VER>"` if it changed.

- [ ] **Step 4: Run the existing pi version-check test**

```bash
uv run pytest tests/harness/test_pi_version_check.py -v
```

Expected: pass. If the version string changed, the test should still pass (it asserts equality with `pi_expected_version`).

- [ ] **Step 5: Commit (only if anything changed)**

```bash
git add .tool-versions src/relay/config.py
git commit -m "build: align pi version pin with relay-bridge tag"
```

Skip the commit if no files actually changed.

---

## Phase 4 — Tests

The relay test suite can't directly assert the subscription path works (that requires live OAuth). What it CAN assert: the Dockerfile invariants (Task 2.1), the runtime sanity check, and the manual acceptance procedure.

### Task 4.1: Extend the acceptance-testing doc with a subscription-path verification

**Files:**
- Modify: `docs/acceptance-testing.md`

- [ ] **Step 1: Add a "Subscription path verification" subsection**

Find an appropriate location in `docs/acceptance-testing.md` (after any existing pi-related items). Add:

```markdown
### Subscription path verification (ADR-52)

After deploying a new image to the LXC, verify that `PI_AGENT_SDK=1`
actually routes to the Max subscription rather than extra usage:

- [ ] On the deployed container, run a one-shot pi call:

  ```bash
  ssh agent 'docker exec relay sh -c "PI_AGENT_SDK=1 pi -p \"reply with OK\" --mode json --provider anthropic --model claude-sonnet-4-5 2>&1 | head -3"'
  ```

  Expected: first three lines are `session`, `agent_start`, `turn_start`
  event types. If the first turn instead carries
  `errorMessage: "400 ... You're out of extra usage"`, the bridge is
  not active — the image was built from the wrong ref, the bridge
  artefacts are missing, or PI_AGENT_SDK env was lost.

- [ ] On `claude.ai/settings/usage`, observe a real assistant turn
  through the dashboard. The "Max" usage bar should advance; the
  "Extra usage" bar should stay flat for that turn.
```

- [ ] **Step 2: Commit**

```bash
git add docs/acceptance-testing.md
git commit -m "docs(acceptance): add subscription-path verification for ADR-52"
```

### Task 4.2: No new automated test needed beyond Task 2.1

The Dockerfile-invariant test (`tests/test_dockerfile_invariants.py`) added in Task 2.1 plus the runtime-stage `RUN test -f …` assertion is the full automated coverage. The actual subscription routing requires Anthropic's backend to recognise the client — only verifiable via the acceptance test from Task 4.1.

DO NOT add a unit test that mocks claude-agent-sdk — the test would assert behaviour of code we don't own, and would not catch the real failure mode (missing bridge files in the published image).

---

## Phase 5 — Documentation

### Task 5.1: Append ADR-52 to docs/decisions.md

**Files:**
- Modify: `docs/decisions.md` (append at the bottom)

- [ ] **Step 1: Read the current bottom of decisions.md**

```bash
tail -5 docs/decisions.md
```

Confirm ADR-51 is the last entry and the file ends with a blank line.

- [ ] **Step 2: Append ADR-52**

Add at the end of the file:

```markdown
## ADR-52 — Build pi from `johnmathews/pi` fork to ship the agent-SDK bridge

**Status:** active. Supersedes the npm-install mechanism in ADR-51 (delivery method only — the bundling + ENV decisions in ADR-51 remain in force).

**Date:** 2026-05-31.

**Context.** ADR-51 (2026-05-31, same day) bundled pi into the production image via `npm install -g @earendil-works/pi-coding-agent@0.74.0` and documented the mechanism behind `PI_AGENT_SDK=1` by reading pi's source. The 2026-05-31 LXC deployment exposed a gap between source and npm artefact: a smoke `pi -p` call inside the deployed container returned `400 invalid_request_error: "You're out of extra usage..."` even though `PI_AGENT_SDK=1` was set, valid OAuth tokens were mounted, and `pi --version` reported the pinned 0.74.0.

Investigation (`journal/260531-pi-bridge-fork-build.md`) found that the npm-published `@earendil-works/pi-ai@0.74.0` package omits the bridge file (`dist/providers/anthropic-agent-sdk.js`), omits the `@anthropic-ai/claude-agent-sdk` dependency, and ships a stripped `anthropic.js` that contains no `PI_AGENT_SDK` references. The bridge exists only in the operator's own fork (`johnmathews/pi`), implemented across the W1→W14 commit series — it is *unreleased upstream code*. Upstream `badlogic/pi-mono` has no concept of the bridge at any version (0.74.0 through 0.78.0 inclusive verified).

So ADR-51's mechanism description (steps 1–2) is correct as a reading of pi *source* but does not match pi as published on npm. The image built per ADR-51 has pi present and `PI_AGENT_SDK=1` set but no bridge code to take effect — the dynamic `import("@anthropic-ai/claude-agent-sdk")` is inside a file that doesn't exist in the published artefact.

**Decision.**

1. **Build pi from `johnmathews/pi` at an annotated tag** (`relay-bridge-v1`, currently a clean cherry-pick of W1→W14 + post-W14 fixes on top of upstream `badlogic/pi-mono` main). Tag is annotated and pushed; Dockerfile pins to the tag name and resolves it once at build time via `git fetch --depth 1 origin <tag>`.
2. **Dockerfile build stage**: clone the fork, `npm ci && npm run build && npm prune --omit=dev`, copy the resulting `/opt/pi` tree into the runtime image, create `/usr/local/bin/pi` as a symlink to the built `cli.js`.
3. **Runtime stage gains a bridge-artefact sanity check** (`RUN test -f /opt/pi/packages/ai/dist/providers/anthropic-agent-sdk.js && test -d /opt/pi/node_modules/@anthropic-ai/claude-agent-sdk`) — fails the image build if a future change accidentally reverts to npm-published pi.
4. **Repo gains `tests/test_dockerfile_invariants.py`** to assert the Dockerfile mentions the fork URL, pins an immutable ref, and copies `/opt/pi`. CI catches drift before image build runs.
5. **Bumping pi**: rebuild the relay-bridge branch on a newer upstream tag (Phase 1 of `docs/plans/2026-05-31-pi-bridge-fork-rebuild.md`), push as `relay-bridge-vN`, update `PI_REF` in Dockerfile + `Settings.pi_expected_version` + `.tool-versions` together.

**Alternatives considered.**

- **Vendor pre-built pi tarballs in the relay repo.** Rejected — adds ~10 MB of binary blobs to the relay git history per pi bump, requires a manual `npm pack` step on the operator's laptop (not reproducible from source), and the rebuild ritual becomes "build locally, repack, commit" instead of "push a fork tag." Reproducibility loss outweighs the saved 1-3 minutes of CI build time.
- **Publish the fork as `@johnmathews/pi-coding-agent` on a personal npm scope.** Rejected — adds an npm publishing pipeline for a single consumer (this relay image). The fork is a single-tenant artefact; npm is overkill.
- **Open a PR to `badlogic/pi-mono` upstreaming the bridge.** Worth pursuing in parallel, but does not unblock today's deployment — upstream merge timeline is uncontrollable and the bridge as currently designed may not match what upstream wants. Tracked as a long-term task, not a blocker for ADR-52.
- **Switch the production harness to `@anthropic-ai/claude-agent-sdk` directly.** Rejected — ADR-09 chose pi because claude-agent-sdk has tool-call timeout constraints that don't fit relay's longer-running tool calls. Re-evaluation is its own project, not a hotfix for the billing path.
- **Run a host-side credential proxy** that injects `Authorization: Bearer <oauth-token>` for the container's outbound `api.anthropic.com` traffic. Rejected — this is a defence-in-depth security pattern (keep secrets off the container), not a routing mechanism. The container would still fall back to extra-usage because the missing bridge files, not the credential location, are the root cause.

**Consequences.**

- **Image build now depends on `johnmathews/pi` being reachable.** Hard requirement for CI. Fork is public; SHA pin makes the artefact immutable. Backup: mirror the repo to a second remote if the bus factor matters.
- **Image build time grows by ~1-3 minutes** for the pi-mono build (clone + `npm ci` + sequential per-package build). Layer-cached by `ARG PI_REF`, so most rebuilds skip it.
- **Image size grows by ~100-200 MB** for the bundled pi tree (built dist + production node_modules + the platform-matched `claude` native binary).
- **Pi bumps become a 2-step ritual**: rebuild the fork branch + bump the relay tag/SHA. Less ergonomic than a single npm pin, but the only path that produces a working subscription image.
- **The fork dependency is structural and bus-factor sensitive.** ADR-52 is honest about this — the bridge is not in upstream; if `johnmathews/pi` becomes unmaintained, the relay image's subscription path goes with it. The mitigation is the open-source path (upstream the bridge or publish the fork formally), tracked separately.
- **ADR-51's other decisions stay in force**: bundle pi into the image (yes — just from a different source), set `ENV PI_AGENT_SDK=1` (yes), bind-mount `~/.pi` for OAuth (yes), keep credentials per-user (yes), pin the version (yes, via `relay-bridge-vN` tag instead of an npm version string).

**Related ADRs:** ADR-04 (harness layer is the only code that knows pi exists — fork choice doesn't change that); ADR-09 (Max-subscription auth path; the bridge is now its actual delivery mechanism); ADR-16 (pi version pin — now tracked via the fork tag); ADR-30 (Phase 8 packaging); ADR-51 (this ADR supersedes the *delivery method* — bundling pi via npm — while preserving every other ADR-51 decision; ADR-51's mechanism description is correct for pi source but not for the npm artefact, see Context).
```

- [ ] **Step 3: Add a status note to ADR-51 pointing at ADR-52**

In `docs/decisions.md`, find the ADR-51 header. Add a status line directly under it:

```markdown
## ADR-51 — Bundle pi into the production image; mount host `~/.pi` for auth

**Status:** active for the bundling + ENV + auth-volume decisions. Delivery method (`npm install -g @earendil-works/pi-coding-agent`) is superseded by ADR-52 — the npm-published package strips the agent-SDK bridge. See ADR-52 for current delivery.
```

(Insert above the existing `**Status:** active.` line; do not delete the existing line. The CLAUDE.md append-only rule means we *amend* status, not rewrite the body.)

- [ ] **Step 4: Commit**

```bash
git add docs/decisions.md
git commit -m "docs(adr): add ADR-52 fork-build delivery; flag ADR-51 delivery superseded"
```

### Task 5.2: Update `docs/harness.md` PI_AGENT_SDK mechanism paragraph

**Files:**
- Modify: `docs/harness.md` around line 99

- [ ] **Step 1: Read the current section**

```bash
sed -n '90,115p' docs/harness.md
```

- [ ] **Step 2: Append a note pointing at ADR-52**

After the existing "What `PI_AGENT_SDK=1` actually flips" paragraph (which reads pi's source), add a new paragraph:

```markdown
**Where the bridge ships from** (ADR-52): the npm-published `@earendil-works/pi-ai` package excludes the bridge file (`anthropic-agent-sdk.js`) and the `@anthropic-ai/claude-agent-sdk` dependency. Relay's production image therefore builds pi from `johnmathews/pi` at the `relay-bridge-vN` tag instead of `npm install`-ing it. The bridge code, the dependency, and the platform-matched `claude` native binary all ship via the build stage. See ADR-52 and `docs/plans/2026-05-31-pi-bridge-fork-rebuild.md` for the rebuild procedure.
```

- [ ] **Step 3: Commit**

```bash
git add docs/harness.md
git commit -m "docs(harness): point PI_AGENT_SDK mechanism at ADR-52 delivery"
```

### Task 5.3: Update `docs/getting-started.md` §8 (Docker)

**Files:**
- Modify: `docs/getting-started.md` around line 189

- [ ] **Step 1: Read the current Docker section**

```bash
sed -n '180,220p' docs/getting-started.md
```

- [ ] **Step 2: Add a "Bridge provenance" subsection**

After the existing `PI_AGENT_SDK=1` mention in §8, add:

```markdown
> **Why the image is large and the build is slow.** The production
> image builds pi from `johnmathews/pi` at the pinned `relay-bridge-vN`
> tag rather than from npm. The npm-published pi packages strip the
> `@anthropic-ai/claude-agent-sdk` bridge — without it, `PI_AGENT_SDK=1`
> silently falls back to extra-usage billing instead of the Max
> subscription. See ADR-52 for the full rationale and
> `docs/plans/2026-05-31-pi-bridge-fork-rebuild.md` for the bump
> procedure if you want to track a newer upstream.
```

- [ ] **Step 3: Commit**

```bash
git add docs/getting-started.md
git commit -m "docs(getting-started): explain fork-build provenance"
```

### Task 5.4: Update `docker-compose.example.yml` comments

**Files:**
- Modify: `docker-compose.example.yml`

- [ ] **Step 1: Read the current header comment block**

```bash
sed -n '1,40p' docker-compose.example.yml
```

- [ ] **Step 2: Update the "image bundles pi 0.74.0" line**

Find the comment that says `The image bundles pi 0.74.0`. Replace it with:

```yaml
# Runs the relay backend (which also serves the built dashboard at
# http://localhost:7800/). The image bundles pi built from
# johnmathews/pi:relay-bridge-vN — NOT the npm-published pi, which
# strips the agent-SDK bridge needed for Max-subscription routing
# (see ADR-52). Pi's Max-subscription OAuth token must be supplied
# via a host volume (see the `~/.pi` mount below). Copy to
# docker-compose.yml and adjust.
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.example.yml
git commit -m "docs(compose): note fork-build pi (ADR-52)"
```

### Task 5.5: README.md update (if needed)

**Files:**
- Modify: `README.md` (only if it mentions pi version or "bundles pi 0.74.0")

- [ ] **Step 1: Scan README for pi mentions**

```bash
grep -n -E 'pi 0\.74|pi-coding-agent|PI_AGENT_SDK|@earendil-works' README.md
```

- [ ] **Step 2: If any matches**, update wording to either reference the relay-bridge tag or link to ADR-52 / `docs/getting-started.md`. Keep the change minimal.

- [ ] **Step 3: Commit (if anything changed)**

```bash
git add README.md
git commit -m "docs(readme): align pi mentions with ADR-52"
```

### Task 5.6: Journal entry

**Files:**
- Create: `journal/260531-pi-bridge-fork-build.md`

- [ ] **Step 1: Write the entry**

Per the global CLAUDE.md convention (`yymmdd-descriptive-name.md`):

```markdown
# 2026-05-31 — Pi bridge fork-build (ADR-52)

## What

Deployed relay container on the agent LXC was billing pi calls against extra usage instead of the Max subscription, despite ADR-51 having shipped earlier the same day with `PI_AGENT_SDK=1` set in the image ENV and a working `~/.pi` bind-mount.

## Root cause

The bridge code that makes `PI_AGENT_SDK=1` route to Max — `packages/ai/src/providers/anthropic-agent-sdk.ts` and its dynamic `import("@anthropic-ai/claude-agent-sdk")` — does not exist in the npm-published `@earendil-works/pi-ai@0.74.0` package. Verified by downloading the tarball directly from `https://registry.npmjs.org/@earendil-works/pi-ai/-/pi-ai-0.74.0.tgz` and grepping: no `PI_AGENT_SDK` references, no `claude-agent-sdk` strings, no `anthropic-agent-sdk.js` file. Same story for `pi-ai@0.78.0` (latest at time of writing).

The bridge lives only in `johnmathews/pi`, implemented across W1→W14 + post-W14 fixes (~20 functional commits). Upstream `badlogic/pi-mono` has no concept of the bridge at any version.

## Decision

Build pi from the fork at a clean cherry-pick tag (`relay-bridge-v1`) rather than `npm install`ing the published package. The fork branch is a clean cherry-pick of bridge-essential commits onto fresh upstream main — no docs/journal/eng-team noise — pushed as an annotated tag so the Dockerfile can pin to an immutable ref.

## Outcome

ADR-52 records the decision; the Dockerfile fork-build stage replaces the npm install; a runtime-stage `RUN test -f …` assertion + `tests/test_dockerfile_invariants.py` guard against accidental reverts. Image build time grows by 1-3 minutes and image size by ~100-200 MB; both acceptable for a self-hosted single-tenant deployment.

## Surprising thing learned

The mechanism behind `PI_AGENT_SDK=1` is not a routing trick — it's that `claude-agent-sdk` spawns the bundled `claude` binary as a subprocess and Anthropic's backend identifies that subprocess as Max-eligible by its client signature. The OAuth token is the same; only the *client identity* differs. This is documented in pi's investigation doc (`.engineering-team/anthropic-max-billing-investigation.md`, B.5) and confirmed by the W5 commit message ("SDK package identity is the lever").
```

- [ ] **Step 2: Commit**

```bash
git add journal/260531-pi-bridge-fork-build.md
git commit -m "journal(260531): pi bridge fork-build (ADR-52)"
```

---

## Phase 6 — Deploy + verify

### Task 6.1: Push the relay branch and wait for CI

**Files:** (none — git/CI operation)

- [ ] **Step 1: Push the branch (or open a PR if you prefer review)**

```bash
git push origin <branch>
```

If pushing to `main` directly, the existing `.github/workflows/ci.yml` builds and publishes to `ghcr.io/johnmathews/relay:latest`.

- [ ] **Step 2: Wait for CI to complete**

```bash
gh run watch
```

Expected: green. The pi build adds ~1-3 minutes to image build time.

### Task 6.2: Pull and restart on the LXC

**Files:** (none — operational)

- [ ] **Step 1: Pull the new image on the LXC**

```bash
ssh agent 'docker compose -f /srv/apps/relay/docker-compose.yml pull && docker compose -f /srv/apps/relay/docker-compose.yml up -d'
```

(Substitute the actual compose file path on the LXC — check `home-server/proxmox-setup/roles/agent_lxc/tasks/main.yml` if uncertain.)

- [ ] **Step 2: Verify the container is running the new image**

```bash
ssh agent 'docker inspect relay --format "{{.Image}} created={{.Created}}"'
```

Expected: image SHA matches the just-pushed `ghcr.io/johnmathews/relay:latest`.

### Task 6.3: Run the subscription-path acceptance test

Follow `docs/acceptance-testing.md`'s new "Subscription path verification" subsection added in Task 4.1. Specifically:

- [ ] **Step 1: Run the smoke pi call inside the deployed container**

```bash
ssh agent 'docker exec relay sh -c "PI_AGENT_SDK=1 pi -p \"reply with OK\" --mode json --provider anthropic --model claude-sonnet-4-5 2>&1 | head -3"'
```

Expected: `session`, `agent_start`, `turn_start` event lines — NOT a `400 "out of extra usage"` line.

- [ ] **Step 2: Verify Anthropic console reflects subscription usage**

Open `https://claude.ai/settings/usage` in a browser. Confirm:
- The Max subscription bar has advanced after the smoke call.
- The "Extra usage" bar has NOT advanced.

If the smoke call goes through but extra-usage advances, the OAuth token on the LXC may be bound to a different account than the laptop's — log in again on the LXC with `sudo -u "#1000" env PI_AGENT_SDK=1 HOME=/srv/apps/relay pi`.

- [ ] **Step 3: Drive one end-to-end relay run as the final check**

Open the deployed dashboard at `http://192.168.2.107:7800/`, start a small task run (a 1-2 iter relay run is enough), and confirm via `claude.ai/settings/usage` that the iters bill against subscription quota, not extra usage.

### Task 6.4: Update the journal entry with the verified-working note

**Files:**
- Modify: `journal/260531-pi-bridge-fork-build.md`

- [ ] **Step 1: Append a "Verified" section**

```markdown
## Verified

- 2026-05-31 (UTC): live container smoke test on the LXC returned a real `turn_start` (no extra-usage 400). `claude.ai/settings/usage` shows the test turn billed against Max, extra-usage flat.
- One end-to-end relay run completed; Max bar advanced, extra-usage bar did not.
```

- [ ] **Step 2: Commit**

```bash
git add journal/260531-pi-bridge-fork-build.md
git commit -m "journal(260531): record live verification"
```

---

## Risks + rollback

- **Phase 1 cherry-picks may not apply cleanly** if upstream `badlogic/pi-mono` has refactored sibling code since the W1→W14 series. Mitigation: resolve conflicts inline, keep both the bridge change and the upstream improvement; if a single commit becomes too entangled, drop it and stack a fresh fixup commit at the end of the branch.
- **`npm ci` in Phase 1 may fail** if the lockfile has drifted relative to fresh upstream. Mitigation: `npm install` to regenerate, commit the lockfile update as a final cleanup commit on `relay-bridge` before tagging.
- **First Dockerfile build may take 5+ minutes** before layer caching warms up. Subsequent CI builds skip the pi stage entirely unless `PI_REF` changes.
- **Rollback plan**: if the new image breaks production, `docker compose pull` the previous image SHA on the LXC and `up -d`. The previous (ADR-51) image runs fine — it's just billing against extra usage, which is the status quo we started from. Then revert the relay commits and re-deploy.

---

## Self-review

**Spec coverage:**
- Phase 1 restructures the fork ✓
- Phase 2 updates relay Dockerfile ✓
- Phase 3 aligns version pins ✓
- Phase 4 adds acceptance test ✓ (no spurious unit tests per YAGNI)
- Phase 5 documents (ADR-52 + harness + getting-started + compose + journal) ✓
- Phase 6 deploys + verifies live ✓

**Placeholder scan:** no `TBD` / `TODO` / "implement later". The `<PI_VER>` placeholder in Task 3.1 is the only intentional one — it cannot be resolved until Phase 1 runs.

**Type consistency:** `PI_REF`, `PI_REPO`, `/opt/pi`, `relay-bridge-v1`, `relay-bridge` (branch), `johnmathews/pi` are used consistently across all phases. The test in Task 2.1 (`tests/test_dockerfile_invariants.py`) asserts the exact strings the Dockerfile in Task 2.1 introduces.

**One open question for the operator**: do you want to push the relay change directly to `main` (auto-publishes), or via PR for sanity review? The plan assumes direct push — if PR, insert a "open PR + wait for review" step between 5.6 and 6.1.
