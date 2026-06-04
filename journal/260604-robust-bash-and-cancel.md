# 2026-06-04 — Robust bash + cancel arc (Layer 1+2 + Layer 5 fragments)

## What triggered this

Run `20260604-201957-62d5` on `/srv/apps/syncthing/horizons` (a horizons
WU0.1 scaffold prompt) wedged the deployed relay container on the agent
LXC. The dashboard showed "live · 5m 35s ago" but the events table was
silent and **Cancel run** returned 200 OK without finalising the row.
Diagnosis surfaced five linked failure modes:

1. **`npm run dev` hangs pi forever** — pi's bash tool has no
   duration cap and no concept of "this is a long-running server".
2. **Cancel doesn't finalise the run** — pi got SIGTERM, but its
   descendants (vite via npm) inherited pi's stdout fd and kept the
   pipe alive. `PiSession.events()`'s `async for raw in self._proc.stdout`
   never hit EOF, `_drive_iter`'s `finally` never fired,
   `LoopResult("cancelled")` never returned. Run row stuck `running`
   until container restart (ADR-31 orphan sweep).
3. **In-flight tool calls are invisible** between `iter_started` and
   turn-end — only ephemeral ADR-46 deltas, no canonical events.
4. **`RunHealthBadge` says "live" despite ~5m of silence** — the
   classifier read heartbeat `receivedAt` (which advances every 5s
   regardless of activity) instead of `lastEventTs`.
5. **Orphan processes (`npm`/`node`/`vite`) survive cancel** and
   continue holding ports + the worktree.

## How we got out of it (in-session)

Live diagnosis on the agent LXC (`ssh agent` → `docker exec relay …`):
queried `/proc` enumeration showed pi + claude-agent-sdk + a full
npm/node/vite orphan tree all alive. The events table had exactly two
rows (`run_started`, `iter_started`) at 20:19:57 — 8 minutes of
silence under "live". Sampling the SSE stream confirmed: heartbeats
flowing at 5s cadence with `last_event_ts: 20:19:57` (frozen at
iter-start). User restarted the container (ADR-31 sweep finalised the
row as `failed: internal_error`).

## Design

Five-layer defense documented in
`docs/plans/2026-06-04-robust-bash-and-cancel.md`. Master mechanism:
**per-iter process-tree isolation**. Once pi owns its own session/pgid,
killpg at iter end (cancel or normal termination) reaps everything
including dev servers, freeing the agent from manual cleanup
responsibility.

Brainstorming settled on the hybrid lifetime model (relay enforces
teardown at iter end; agent calls tools; cross-iter survival is not a
goal — fresh-context-per-iter (ADR-20) makes cross-iter handles
impossible anyway).

## What shipped in this PR

**Three tasks across two domains**, eight commits including review
follow-ups.

### Task 1 — Layer 1+2 (harness)

- `PiHarness.spawn` adds `start_new_session=True` → pi becomes session
  leader, `pgid == pid`.
- `PiSession.cancel` switches from `self._proc.terminate()` to
  `os.killpg(pgid, SIGTERM)` → 5s wait → `os.killpg(pgid, SIGKILL)`,
  with `contextlib.suppress(ProcessLookupError, PermissionError)`
  around each killpg (EPERM covers the rare pgid-reuse race; ESRCH
  covers the pi-already-exited race).
- New regression test `test_pi_session_cancel_kills_descendants` uses
  a shell fixture (`orphan_holder.sh`) that forks a backgrounded
  `sleep 100`, announces its PID via JSONL on stdout, then
  `exec sleep 100`. The test verifies pgid identity (Layer 1) and that
  the descendant is reaped within 2s of cancel (Layer 2). The fixture
  also handles `--version` so `_maybe_check_version` doesn't hang.

### Task 2 — Layer 5a (RunHealthBadge)

- Classifier anchors on `lastEventTs` instead of heartbeat `receivedAt`
  (the bug — `receivedAt` ticks every 5s regardless of pi activity).
- Fifth state `disconnected` when heartbeats themselves stop arriving
  (`nowMs - receivedAt > HEARTBEAT_GAP_MS = 20_000`) — distinct from
  `stalled` (heartbeats flowing, pi silent). `HEARTBEAT_GAP_MS` is 4×
  the backend's `_KEEPALIVE_S=5s` (3-miss grace). Coupled comment
  references `api/events.py`.
- `closed` added to the badge's `TERMINAL` set (ADR-50). This is a
  sixth list distinct from the CLAUDE.md five-list TERMINAL sync
  rule, but the semantics must agree.
- 5 new vitest cases (3 anchor/state, 1 stalled-via-lastEventTs
  aria-label, 1 closed-status).

### Task 3 — Layer 5b (ToolCallCard)

- Optional `startedAt: string | number | null` prop, self-managed
  `setInterval(nowMs.value = Date.now(), 1000)` that ONLY mounts when
  the card is pending AND `startedAt` is set, plus a `watch(isPending)`
  that clears the interval when result arrives reactively (the
  `onMounted` guard alone leaves the interval running for the card's
  lifetime once `result` lands on the live-stream path — caught in
  review).
- Renders `running Ns` in two placements: head row when `!embedded`,
  inline-block leading chip when `embedded` (TimelinePane mode).
  `tabular-nums` keeps the chip width stable across digit-count
  changes.
- `StreamEvent.ts: string | undefined` propagation through both SSE
  ingest (`stores/events.ts:onSseEvent`) AND REST replay
  (`loadReplay`) — required for `:started-at="row.event.ts"` on the
  `TimelinePane.vue:1160` invocation to type-check. Plan gap closed
  surgically (one optional field, two ingest lines).
- 5 new vitest cases (pending + tick, ISO parse, result hides chip,
  null hides chip, stop-on-result-arrival).

## Cross-cutting trap block

Added to `CLAUDE.md` following the established 9f / chat-mode W6 /
pause-for-review trap pattern. Documents the three new load-bearing
invariants: PiHarness session/killpg pairing, dual-path
`StreamEvent.ts` propagation, RunHealthBadge `lastEventTs` anchoring +
`HEARTBEAT_GAP_MS` derivation from `_KEEPALIVE_S`.

## What stayed deferred

Three follow-up PRs sequenced after this one (each requires a pi-fork
retag + `PI_REF` bump):

- **Layer 3 — `mcp__pi__bash` foreground timeout** (default 120s, cap
  600s). Tag `relay-bridge-v2`. Pi fork only.
- **Layer 4 — `mcp__pi__bash_background` tool family** (start /
  tail / kill). Lifetime bounded by iter (Layer 2 killpg reaps).
  Tag `relay-bridge-v3`. Pi fork only.
- **Layer 5 (rest)** — engineering-team skill addition documenting
  the tools, plus a `RELAY_BG_REMINDER:` line in the task-mode
  preamble. Paired with Layer 4 (no skill point in referencing tools
  that don't exist yet).

The design doc remains active at
`docs/plans/2026-06-04-robust-bash-and-cancel.md` as the canonical
reference for these follow-ups.

## Process notes

- **Subagent-driven execution** (superpowers skill) ran one
  implementer + spec reviewer + code-quality reviewer per task. Two
  important issues surfaced per task on average; all addressed via
  same-task "polish" commits (separate from the feature commits for
  audit trail) before moving to the next task.
- **Plan gaps caught by implementers**: (a) Task 1's fixture needed a
  `--version` guard because `_maybe_check_version` calls
  `pi_bin --version` and would hang; (b) Task 3 needed `StreamEvent.ts`
  because `row.event.ts` wasn't typed. Both were minimal, scope-correct
  additions documented in the implementer's report and validated by
  the spec reviewer.
- **Plan timing arithmetic was slightly wrong** in Steps 2.1 and 3.1
  (the `setSystemTime(FIXED_NOW + N_000)` then `advanceTimersByTimeAsync(1_000)`
  pattern adds 1s to the elapsed total). Implementers correctly
  adjusted to `+19_000`/`+4_000` to land the intended threshold.
- **Final cross-cutting review (opus)** flagged that the project's
  CLAUDE.md cross-cutting-trap tradition was unfulfilled — added the
  block in a follow-up commit before merge.

## Acceptance

Final gate on the merged `main` (`c14bce3`):
- Backend: ruff clean, mypy --strict clean, 414 passed / 3 skipped,
  95% coverage.
- Frontend: eslint clean (`--max-warnings 0`), `vue-tsc` clean,
  499/499 vitests pass.
- Manual acceptance smoke deferred to the next deployment of the
  agent LXC container: start a small run, hit Cancel mid-iter,
  verify the row flips to `cancelled` within 10s without container
  restart.
