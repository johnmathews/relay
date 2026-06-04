# Robust bash + cancel arc — design

**Status:** design accepted 2026-06-04, awaiting implementation plan.

## Motivation

Run `20260604-201957-62d5` on the deployed relay container exposed five
linked failure modes. The agent emitted `npm run dev` as a bash tool
call; vite kept running; pi sat waiting for the bash call to return
forever. The operator hit **Cancel** twice — both returned 200 — but
the run row stayed `running` and the dashboard health badge kept
reading "live" despite the events table being silent for ~8 minutes.
Only a container restart cleared the row (ADR-31 orphan sweep).

Five concrete bugs, one architectural gap:

1. **`npm run dev` hangs pi forever.** The pi bash tool has no
   duration cap and no notion of "this is a long-running server."
   Anything that doesn't exit eventually wedges the whole iter.
2. **Cancel doesn't finalize the run.** Pi got SIGTERM, but its
   descendants (`npm` → `vite`) survived as orphans, holding pi's
   stdout fd open. The relay-side `async for raw in self._proc.stdout`
   inside `PiSession.events()` never hit EOF, so `_drive_iter`'s
   `finally` never fired, and `LoopResult("cancelled")` was never
   returned. Run row stuck `running` until container restart.
3. **In-flight tool calls are invisible** in the events log between
   `iter_started` and turn-end. Only ephemeral ADR-46 deltas exist —
   the dashboard reads `last_event_ts` and sees a multi-minute gap
   with no canonical events.
4. **`RunHealthBadge` says "live" despite ~5m of silence.** The
   classifier reads heartbeat *receivedAt* (which advances every 5s
   regardless of activity) instead of `lastEventTs` (which would have
   correctly classified as `stalled` at >60s).
5. **Orphan processes (`npm`/`node`/`vite`) survive cancel** and
   continue holding ports and the worktree.

Architectural gap: **a task-mode iter has no concept of a process
tree.** Pi runs in relay's own process group; everything pi spawns
inherits the same group. There is no per-iter sandbox boundary the
loop can guarantee teardown of.

## Architecture overview

Five layers, each independently useful, all required for "robust +
dependable". Layers 1+2 are the foundation — every layer above
depends on per-iter process-tree isolation existing.

```
┌────────────────────────────────────────────────────────────────────┐
│ Layer 1 — Process-group hygiene (PiHarness.spawn)                  │
│   start_new_session=True → pi is session leader, owns its own pgid │
│   Vite/npm/test runners all live inside this group                 │
├────────────────────────────────────────────────────────────────────┤
│ Layer 2 — Group cancel cascade (PiSession.cancel)                  │
│   os.killpg(pgid, SIGTERM) → wait 5s → os.killpg(pgid, SIGKILL)    │
│   All descendants die together; stdout pipe hits EOF → loop unwinds│
├────────────────────────────────────────────────────────────────────┤
│ Layer 3 — Pi bash tool foreground timeout (pi fork)                │
│   mcp__pi__bash gains required `timeout_s` (default 120, cap 600)  │
│   Returns "killed after Ns" + partial output; pi can decide next   │
├────────────────────────────────────────────────────────────────────┤
│ Layer 4 — bash_background tool family (pi fork, new)               │
│   Fire-and-forget; returns {handle, log_path}; tail-able mid-iter  │
│   Lifetime bounded by iter — Layer 2's cascade reaps at iter end   │
├────────────────────────────────────────────────────────────────────┤
│ Layer 5 — Engineering-team skill rule + UI fixes                   │
│   Skill: "Never run a server with bash; use bash_background"       │
│   Frontend: RunHealthBadge state classifies on lastEventTs         │
│   Frontend: timeline shows "tool X running for Ns" between         │
│             tool_use_start and tool_use_end                        │
└────────────────────────────────────────────────────────────────────┘
```

Operating principle: **the agent never has to remember to clean up.**
Relay enforces it via process-group teardown at iter end. The agent
just calls tools; the harness layer guarantees containment.

## Layer 1 — Process-group hygiene

**File:** `src/relay/harness/pi.py` — `PiHarness.spawn`.

Add one kwarg to the existing `asyncio.create_subprocess_exec` call:

```python
proc = await asyncio.create_subprocess_exec(
    *argv,
    cwd=str(cwd),
    env=full_env,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    limit=self._settings.pi_stdout_limit,
    start_new_session=True,   # NEW: pi becomes session leader + own pgid
)
```

`start_new_session=True` calls `setsid()` post-fork in the child. Pi
becomes the session leader of a new session and process group with
`pgid == pid`. Any subprocess pi forks — claude-agent-sdk, bash, npm,
vite — joins this group unless it explicitly does its own `setsid`.

**Behavioural impact on pi:** pi loses its controlling terminal. Pi
`--mode json` is non-interactive (relay already redirects stdin via
the asyncio subprocess machinery), so no functional change. Verified
via smoke test (Layer 1 verification below).

## Layer 2 — Group cancel cascade

**File:** `src/relay/harness/pi.py` — `PiSession.cancel`.

Replace the body to SIGTERM the group, not just pi:

```python
async def cancel(self) -> None:
    self._cancelled = True
    if self._proc.returncode is not None:
        return
    pgid = self._proc.pid  # pi is session leader: pgid == pid
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        await asyncio.wait_for(self._proc.wait(), timeout=5)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        await self._proc.wait()
```

`os.killpg(pgid, signal)` sends to every process in the group. SIGTERM
gives shutdown handlers a chance; SIGKILL after 5s guarantees teardown.
`ProcessLookupError` suppression handles the race where pi exits
between the rc check and the kill.

After SIGKILL on the group, all members die, all fds close, the pipe's
write end is gone — pi's stdout pipe hits EOF on the relay side, the
`async for raw in self._proc.stdout` in `PiSession.events()` unwinds,
`_drive_iter`'s `finally` runs, the loop returns
`LoopResult("cancelled")`, `RelayCore._apply_result` writes
`run_ended: cancelled`, and the DB row flips. **The entire cancel
finalisation path works again** — bug #2 resolved as a side effect of
fixing the process tree.

Bug #5 (orphan reaping) is the same fix.

**Known escape:** a descendant that does its own `setsid` creates a
new session and escapes `killpg` against pi's pgid. None of pi's
current tools do this. If it becomes a problem, the workaround is to
walk `/proc/<pid>/task/<tid>/children` recursively and SIGKILL each.
Deferred until a real escape is observed.

## Layer 3 — Pi bash tool foreground timeout

**Repo:** `johnmathews/pi` (fork). Tag `relay-bridge-v2` after merge.

The `mcp__pi__bash` MCP tool gains a required `timeout_s` arg:
default 120, hard cap 600. When the wall-clock fires:

1. Send SIGTERM to the spawned bash subtree (pi-side `start_new_session`
   at this layer too — symmetric with Layer 1).
2. Wait 2s.
3. SIGKILL.
4. Return `{exit_code: -1, timed_out: true, killed_after_s,
   partial_stdout, partial_stderr}`.

Pi emits this as a normal `tool_execution_end` event with
`isError: true`. Relay persists it as a `tool_use_end` row via the
existing `EventStore.store_harness_event` path — **no
harness/orchestrator change required.**

Default 120s covers most legitimate task-mode work: `uv sync` warm,
`pytest`, `npm install` on a warm cache, `ruff check`. 600s is the
agent's escape hatch for genuinely long ops (cold `npm install`,
large `uv lock`). Agent opts in per-call.

## Layer 4 — `bash_background` tool family

**Repo:** `johnmathews/pi` (fork). Tag `relay-bridge-v3` after merge.

Three new MCP tools — explicit affordance for "I need to start a
long-lived process":

```
mcp__pi__bash_background({ command: string })
  → { handle: string, log_path: string, pid: number }
  Spawns `bash -c <command>` with stdout+stderr → <cwd>/.pi/bg/<handle>.log
  Inherits pi's pgid (Layer 1) → reaped at iter end by Layer 2.
  Returns immediately, no wait.

mcp__pi__bash_background_tail({ handle: string, lines?: number = 50 })
  → { log_lines: string[], pid_alive: boolean, exit_code: number | null }
  Tail of the log file; check liveness; if exited, return exit code.

mcp__pi__bash_background_kill({ handle: string })
  → { killed: boolean }
  SIGTERM, 2s grace, SIGKILL. Idempotent.
```

**Lifetime bound to iter.** The agent does not have to remember to
kill — Layer 2's `killpg` at iter close reaps everything in pi's
group, including backgrounded processes. `_kill` exists for "I'm done
mid-iter and want to free the port for a follow-up step within the
same iter".

**Cross-iter survival is not supported** — fresh context per iter
(ADR-20) means the next iter has no handle anyway. The agent must
start, use, stop within one turn. Documented in the skill (Layer 5).

**Log file location:** `<cwd>/.pi/bg/<handle>.log` under pi's cwd
(the worktree). Cleaned up by the worktree's own teardown at
run-terminal; no separate cleanup logic needed.

## Layer 5 — Skill + UI fixes

### Skill text

**File:** `skills/engineering-team/pi/references/tools.md` (new file
— mirrors the one-topic-per-file pattern of `sentinels.md`,
`fanout.md`, `worktree.md`).

Single paragraph, terse:

> **Long-running commands.** The `bash` tool has a 120s timeout
> (max 600s via `timeout_s`). To start a dev server, watcher, or
> any process that doesn't exit on its own, use `bash_background` —
> returns a handle + log path. Check progress with
> `bash_background_tail`; stop mid-iter with `bash_background_kill`.
> **Backgrounded processes die when the iter ends** — start, use,
> stop within one turn. Never use `bash` for a dev server; the
> timeout will kill it and you'll lose the partial state.

### Preamble reminder

**File:** `src/relay/orchestrator/preamble.py`.

Add a `RELAY_BG_REMINDER:` line mirroring the existing
`RELAY_SENTINEL_REMINDER:` from ADR-53:

```
RELAY_BG_REMINDER: long-lived processes (dev servers, watchers) MUST
use bash_background; foreground bash has a 120s timeout.
```

Belt-and-braces — the skill is the canonical knowledge channel; the
preamble is the per-iter pre-emptive nudge.

### `RunHealthBadge.vue` classifier fix

**File:** `frontend/src/components/runs/RunHealthBadge.vue` lines
51-63.

Anchor the state classifier on `lastEventTs` (true wall-clock age of
pi output), not heartbeat `receivedAt` (which advances every 5s
regardless of activity). Add a fifth state `disconnected` for the
case where heartbeats themselves stop arriving — distinct from "pi is
silent but SSE is alive".

```typescript
const state = computed<
  'connecting' | 'disconnected' | 'live' | 'slow' | 'stalled'
>(() => {
  const hb = props.lastHeartbeat
  if (hb == null) return 'connecting'
  // SSE-level liveness: heartbeats should arrive every 5s. >20s
  // since last heartbeat → SSE is dead, distinct from pi being silent.
  if (nowMs.value - hb.receivedAt > 20_000) return 'disconnected'
  // Pi-level liveness: lastEventTs is the wall-clock of the most
  // recent persisted event. A silent stream stalls this anchor while
  // heartbeats continue.
  const anchor = hb.lastEventTs
    ? Date.parse(hb.lastEventTs)
    : hb.receivedAt
  const age = Math.max(0, nowMs.value - anchor)
  if (age > STALLED_MS) return 'stalled'
  if (age > SLOW_MS) return 'slow'
  return 'live'
})
```

`disconnected` styling: grey, no pulse — matches the visual language
of "no signal" vs `stalled`'s red "signal says nothing".

### Timeline in-flight tool-call badge

**File:** `frontend/src/components/runs/ToolCallCard.vue`.

When the card renders a `tool_use_start` event that has no matching
`tool_use_end` in the same iter, render a ticking "running for Ns"
badge using the same `nowMs` ref pattern as `RunHealthBadge`. Cuts "looks frozen, is working" anxiety during long
foreground commands (test runs, npm install).

## Verification

Each layer gets its own gate. The full Python (`ruff`/`mypy`/
`pytest`) + frontend (`npm run check`) gate per ADR-26 still applies.

**Layer 1+2 — process group + cancel cascade:**
- Unit (`tests/harness/test_pi_session_cancel_kills_descendants.py`):
  spawn a scripted "pi" stand-in that forks a backgrounded
  `sleep 100`, send `session.cancel()`, assert the sleeper PID is
  gone within 1s.
- Integration (`tests/orchestrator/test_cancel_finalizes_run.py`):
  fake harness that holds a stdout fd open via a child after the
  parent dies, cancel the run, assert `run_ended: cancelled` event
  lands AND `runs.status == 'cancelled'` within 10s.
- Smoke (`PI_INTEGRATION=1`): real pi spawn with a prompt that
  backgrounds `python -m http.server 8765`; cancel mid-iter; assert
  no listener on 8765 within 5s.

**Layer 3 — pi bash timeout:**
- Pi-fork test: `bash({command: "sleep 30", timeout_s: 2})` returns
  `timed_out: true` in ≤3s with `killed_after_s ≈ 2`.
- Relay integration (`PI_INTEGRATION=1`): a real pi spawn with a
  prompt that runs `bash({command: "sleep 60", timeout_s: 3})`
  surfaces a `tool_use_end` with `is_error: true` and the iter
  continues.

**Layer 4 — bash_background family:**
- Pi-fork tests for each tool: handle uniqueness, log file content,
  tail behaviour, kill idempotency.
- Relay integration: spawn pi with a prompt that backgrounds
  `python -m http.server 8766`, complete the iter via a `done`
  signal, assert no listener on 8766 after `iter_ended` lands
  (Layer 2's cascade reaped it).

**Layer 5 — skill + UI:**
- Skill: manual review; engteam template smoke test.
- Vitest `RunHealthBadge`: given `lastEventTs` 75s old → `stalled`;
  heartbeat 25s stale → `disconnected`; both fresh → `live`.
- Vitest timeline: `tool_use_start` without matching `_end` renders
  a ticking "running for Ns" badge.

## PR sequencing

| PR | Scope | Repo | Independently shippable? |
|---|---|---|---|
| PR1 | Layer 1+2 | relay | ✅ — unsticks today's bug in-process without a container restart |
| PR2 | Layer 3 | pi fork | ✅ — robustness win even without backgrounding |
| PR3 | Layer 4 | pi fork | Paired with PR4 — tools without skill text are unreachable |
| PR4 | Layer 5 | relay + skill | Paired with PR3 |

PR2/3 each requires a pi fork retag (`relay-bridge-v2`,
`relay-bridge-v3`) and a `PI_REF` bump in the relay `Dockerfile` +
`.tool-versions`.

PR1 should land first and standalone — it would have prevented
today's incident from requiring a container restart.

## Risks + tradeoffs

1. **`start_new_session=True` removes pi's controlling tty.** Pi
   `--mode json` is non-interactive, so no impact expected. Verify
   with the Layer 1 smoke test before merging.

2. **`killpg` won't reach descendants that do their own `setsid`.**
   None of pi's current tools do this; documented as a known escape.
   Workaround if it surfaces: walk `/proc/<pid>/task/<tid>/children`
   recursively and SIGKILL each. Deferred until observed.

3. **Bash timeout default of 120s may be too low for cold caches.**
   Configurable per-call (max 600s); default is the safe choice for
   the common case. Agent opts in for known-long ops.

4. **Layer 4 forks the pi-fork further** — 3 new MCP tools. Per
   saved memory (`pi-fork-bridge`, `adr-52-cross-cutting-traps`), the
   fork-as-single-tenant bus-factor concern applies. Mitigation: one
   cohesive commit, surgical diff, the skill is the only knowledge
   channel — if the tools vanish in a future merge, only the skill
   text needs updating.

5. **Heartbeat-only "disconnected" state is a UI fifth state.** Adds
   styling burden but the semantics are genuinely distinct (SSE down
   ≠ pi silent). Worth the cost; the original 4-state design was
   already incomplete.

6. **`bash_background` log files accumulate** under `.pi/bg/`. Per
   iter they share the worktree's lifetime, but a long run with many
   iters could leave many logs. Acceptable — worktrees are reaped on
   run terminal anyway (ADR-44 / existing teardown). If this becomes
   a problem, add per-iter subdirs.

## Open questions

None at design-acceptance. The hybrid lifetime model — agent calls
tools, relay enforces teardown at iter end — closes the
"who-cleans-up?" ambiguity that drove most of the original failure
modes.

## Out of scope

- **Multi-user / multi-tenancy** — relay remains single-user single-
  container per ADR-12; no per-user isolation needed beyond the
  per-iter process group.
- **Persistent backgrounded processes across iters** — not supported
  by design; fresh context per iter (ADR-20) makes cross-iter
  handles impossible anyway.
- **A general-purpose "sandbox" tool family** — Layer 4 is
  intentionally minimal (3 tools, log files only). Containers,
  network namespaces, etc. are not in scope.
- **Replacing pi entirely** — the diagnosis is about pi's bash
  semantics, not pi itself. Pi remains the harness per ADRs
  04/16/52.
