# 2026-06-04 — Resilient iter close arc (ADR-53)

## What triggered this

Run `20260604-174717-09d7` on `/Users/john/projects/horizons` hit the clean+no-signal cliff.
Pi emitted `unit-done` for W0.0, then in the same turn drafted a W0.1 proposal inline and
ended with conversational "OK to proceed?" — no `[[engteam:…]]` closing sentinel. The
loop's `if signal is None:` branch had a single exit: `failed`. The run died, the work was
partially done, and the only recovery was hand-editing the DB or starting over.

The failure mode had two faces: first, the agent genuinely forgot (conversational tone
overwrote the mechanical requirement); second, the preamble gave no in-context reminder
of the four terminal sentinels. Both were fixable without touching the sentinel grammar.

## What landed (WU1–WU7)

- **WU1** — `skills/engineering-team/pi/phases/phase-3-development.md`: "Closing sentinel
  is mandatory" rule + explicit callout that conversational sign-off text is NOT a
  substitute for `pause-for-input`.
- **WU2** — `src/relay/orchestrator/preamble.py`: `RELAY_SENTINEL_REMINDER:` line added to
  every task-mode preamble. Pre-emptive nudge delivered before every iter.
- **WU3** — `src/relay/orchestrator/loop.py`: corrective recovery iter on clean+no-signal.
  One-shot `pending_recovery` flag, `effective_max += 1` (not a budget consumption),
  `_RECOVERY_BODY` injected, `iter_ended.payload.recovery_iter = true`. Tests in
  `tests/orchestrator/test_recovery_iter.py`.
- **WU4** — loop: auto-pause fallback when the recovery iter also no-signals. Synthesises
  a `pause` signal with `reason="agent_end_no_signal_autopause"` so the run lands in
  `paused` rather than `failed`. Tests in `tests/orchestrator/test_autopause_fallback.py`.
- **WU5a** — `src/relay/core.py` + `src/relay/api/runs.py`: `RelayCore.reopen_failed_as_paused`
  + `POST /api/runs/{id}/reopen`. Synthesises a paused iter row in the same DB transaction
  so `resume_run` has a `signal_kind="pause"` iter to match against. Tests in
  `tests/api/test_reopen_run.py` (5 tests, including round-trip).
- **WU5b** — `frontend/src/lib/queries.ts` + `frontend/src/components/runs/layout/RunRightPane.vue`:
  `useReopenRunMutation` + "Reopen as paused" button, gated on
  `status === 'failed' AND last_iter.exit_reason ∈ {agent_end_no_signal, agent_end_no_signal_autopause}`.
  7 new tests in `RunRightPane.spec.ts` + mutation test in `queries.spec.ts`.
- **WU6** — `docs/decisions.md` (ADR-53) + `docs/spec.md` (§6.x Resilient iter close).
- **WU7** — `CLAUDE.md` arc paragraph + this journal entry.

## Design judgements worth recording

**`pending_recovery` flag over body byte-equality.** An early sketch used
`body == _RECOVERY_BODY` to detect "is this iter the recovery shot?". That's wrong:
handoff carry-forward writes agent-authored text into `body`, so a literal string
collision — improbable but possible — would silently mis-tag a normal iter as a recovery
iter, skipping the autopause fallback and burning the recovery budget invisibly. The flag
is in scope for `run_loop` only; it costs nothing.

**Iter-row synthesis on reopen is load-bearing.** `resume_run` queries for the latest iter
with `signal_kind == "pause"`. A reopened run whose last iter has `exit_reason =
"agent_end_no_signal"` has no such row; without the synthesis the reopen endpoint would
succeed (200) but the immediately following resume would 409 with `no_paused_iter`.
The synthesis writes a paused iter row in the same transaction as the status flip, so
the feature is an atomic "reopen → immediately resumable" rather than a partial state
that only works once further backend code arrives.

**`exit_reason` on the iter row is NEVER overwritten.** The historical iter keeps
`exit_reason = "agent_end_no_signal"` after reopen; we only mutate `run.status` and
`run.ended_at` (cleared) plus insert the synthetic paused iter. Audit truth is not
disturbed. Consumers who need to distinguish "this run was reopened" can check for the
synthetic `signal_kind="pause"` + `id` starting with `"reopen-"`.

**`_autopause` suffix on `LoopResult.reason` only, never on `iter.exit_reason`.** The
column tracks what the harness did; the reason field is orchestration / telemetry metadata.
Keeping the column clean means any DB query that groups on `exit_reason` doesn't need to
understand the distinction between a first-time autopause and a reopened-run autopause.

**Marker violations still fail.** Sub-case (3) — `outcome.marker_headline` set OR
non-clean stop — still routes to `failed`. We paper over omissions; we surface bugs.
A half-written sentinel is more informative as a failure than as a silently corrected
pause.

## Still open / TODO

- No live `PI_INTEGRATION` e2e test specifically exercises the recovery iter or the
  autopause fallback. The acceptance gate (ADR-30) is operator-attested; the feature
  will be validated the first time a real engteam run hits the clean+no-signal case.
  **Update**: `PI_INTEGRATION=1 pytest tests/orchestrator/test_pi_e2e.py` ran green
  (2/2 passed) in the WU7 session, though those two existing tests don't directly exercise
  the new sub-cases.
- The dashboard error display on a rejected reopen (409 body text) is minimal — shows the
  raw API error message inline. A future polish pass could map known codes to friendlier
  copy, but the feature is usable as shipped.
- The "Reopen as paused" button is only visible for `exit_reason ∈ {agent_end_no_signal,
  agent_end_no_signal_autopause}`. Pre-arc `failed` runs whose `exit_reason` is something
  else (e.g. `crash`, `timeout`) do not get the button. If a future pattern emerges for
  reopening other failure modes, the gating predicate in `RunRightPane.vue` is the place
  to widen.
