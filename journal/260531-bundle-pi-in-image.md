# 2026-05-31 — Bundle pi into the production image; document `PI_AGENT_SDK=1` mechanism

**Trigger.** Deployment-readiness review: is the GHCR image
(`ghcr.io/johnmathews/relay`) actually deployable as a compose service?
Findings: yes for the API/dashboard/MCP, but the image deliberately
omitted pi — relay spawns pi as a subprocess and the container had no
pi binary, leaving the image non-functional without an undocumented
host-side install step.

**Decision.** Bundle pi (Node 22 + `@earendil-works/pi-coding-agent@0.74.0`)
into the image; bind-mount host `~/.pi` for the per-user OAuth
credential; set `ENV PI_AGENT_SDK=1` in the Dockerfile. Recorded as
ADR-51.

**Sidecar container considered and rejected.** Pi runs as a child
process of relay (`asyncio.create_subprocess_exec`, NDJSON over
stdout). Splitting into two containers would require either a
remote-exec shim, shared-volume IPC, or replumbing the harness to
speak a network protocol — all three violate the ADR-04 harness
boundary. Co-located process spawn is the harness contract.

## What `PI_AGENT_SDK=1` actually does (from pi v0.74.0 source)

Read directly from `/Users/john/projects/pi/packages/coding-agent/src/`:

- **`core/sdk.ts:170` (`buildPiMcpServerForBridge`).** With the flag,
  pi wraps its tool catalogue as an MCP server for the
  `@anthropic-ai/claude-agent-sdk` to consume. Gated additionally on
  the model being `anthropic-messages`, the API key being an OAuth
  token (`sk-ant-oat…` prefix), and a tool runner being present.
  Without the flag, the bridge stays in chat-only fallback — tool
  calls don't fire.
- **`modes/interactive/interactive-mode.ts:3974`
  (`maybeWarnAboutAnthropicSubscriptionAuth`).** Comment in pi's
  source says it plainly:

  > Under PI_AGENT_SDK=1 the OAuth path routes through the
  > @anthropic-ai/claude-agent-sdk bridge against Claude Pro/Max
  > subscription quota (verified W14, investigation doc B.9). The
  > legacy "draws from extra usage" warning is factually wrong in
  > that case — suppress it. When the flag is unset the legacy
  > direct-HTTP path runs and the warning is still accurate
  > (extra-usage routing, currently 400ing under most account
  > states).

So: **no flag → wrong billing route + 400s + no tool calls.** ADR-09
accepted the flag provisionally as "the working path the user
indicated"; findings.md confirmed it empirically; reading pi's source
makes the *mechanism* concrete and explains why the flag is
load-bearing. Recorded in ADR-51 (mechanism is new context; does not
supersede ADR-09's still-provisional verification status — that's a
separate question about long-term billing semantics).

## Auth state and packaging

Pi's per-user state lives at `~/.pi/agent/`:

| Path                     | What it is                                |
|--------------------------|-------------------------------------------|
| `auth.json` (mode 600)   | OAuth token; OAuth refresh writes back    |
| `agent-sdk-config/`      | claude-agent-sdk config                   |
| `agent-sdk-home/`        | claude-agent-sdk home                     |
| `sessions/`              | pi session history (per-iter, ADR-20)     |
| `settings.json`          | user settings                             |

OAuth login requires a browser and so cannot run inside a headless
container. The compose example bind-mounts the host `~/.pi` into
`/home/relay/.pi` read-write (refresh needs write access). One-time
host prereq: `PI_AGENT_SDK=1 pi` on the host to complete the login
before `docker compose up`.

**Uid gotcha.** The image runs as uid 10001 (`relay`); host
`~/.pi/agent/auth.json` is owned by the host uid and is mode 600 — a
straight bind mount makes the file unreadable to the container.
Documented fixes in `docker-compose.example.yml`: chown the host dir
to 10001 (destructive if you also use pi on the host), or override
`user:` in compose to the host uid. macOS Docker Desktop usually
handles this transparently; native Linux compose needs the override.

## Files touched

- `Dockerfile` — new pi-install stage; copy Node binary + pi
  node_modules into the runtime stage; `ENV PI_AGENT_SDK=1`;
  `RUN pi --version` build-time sanity check after `USER relay`.
- `docker-compose.example.yml` — `~/.pi` volume mount; uid note;
  one-time host login prereq header; removed the "pi is NOT bundled"
  caveat.
- `README.md` — Docker section: host prereq, volume mount, ADR-51
  cross-link.
- `docs/getting-started.md` §8 — same treatment.
- `docs/harness.md` "Invocation" — added the `PI_AGENT_SDK=1`
  mechanism explanation (the 3-point summary above) with source
  pointers.
- `docs/decisions.md` — appended ADR-51.
- `CLAUDE.md` — short note that the image now bundles pi (operational
  detail future Claude Code sessions need).

## Open follow-ups (not done in this change)

- The Dockerfile still has a comment referencing the retired
  `relay install-skill` command (line ~38 historically). ADR-44
  superseded that; the comment should be cleaned up next time
  someone is in the file. Out of scope here to avoid scope-creep.
- ADR-09 stays `provisional` — long-term billing semantics
  (subscription cap vs overage) are an Anthropic-side concern this
  ADR doesn't touch. The mechanism is verified; the *commercial*
  status of the path is not.
