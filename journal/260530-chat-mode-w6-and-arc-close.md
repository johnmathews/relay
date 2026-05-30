# 2026-05-30 — Chat mode W6: promote-to-task UI + ADRs + docs (arc close)

Closing day of the chat-mode arc (`docs/archive/2026-05-30-chat-mode-arc.md`,
formerly `docs/proposals/chat-mode.md`). Six work units; W1–W5 had landed in
prior sessions, today shipped W6 plus the archive hygiene.

## What W6 actually did

The chat header's "Promote to task" button was a stub in W4 — it emitted a
`promote` event that the parent `ChatView` caught with an `onPromote` handler
that flashed a 3.5-second notice and did nothing else. W6 swapped the notice
for real navigation.

Three threads landed:

### Thread 1 — promote-to-task UI

Added a pure helper at `frontend/src/lib/promotion.ts` exposing
`buildPromotionPrompt({ events, projectName })`. The fold mirrors the one in
`ChatTranscript.vue` — `pause_resolved.payload.answer` (non-empty) → user
turn, each `iter_started` … `iter_ended` block → assistant turn from
concatenated `assistant_text` with `payload.kind !== 'thinking'`. Tool calls
and other protocol events drop out, same as the chat surface. The output is
a markdown-ish transcript bracketed by `--- Conversation ---` /
`--- End conversation ---` with a one-line context header and a one-line
"continue with the work the chat was building toward" footer.

The fold is deliberately duplicated rather than extracted from
`ChatTranscript.vue` — the transcript needs the iter-id / segment-key
metadata for live-delta merging and stable v-for keys; the promotion
builder only needs concatenated text. Sharing the fold across the two
consumers would have meant a shared interface paying the cost of the
transcript's richer per-segment shape for no benefit on the builder side.
The two stay in sync by colocation (both files cite each other in
comments) and by tests: `frontend/tests/promotion.spec.ts` asserts the
thinking-kind drop, the empty-answer skip, the trailing-empty-assistant
drop, and the live (still-open) iter flush — every fold rule the
transcript renders.

`ChatView.onPromote` now builds the body, writes it to `sessionStorage`
under `relay:promotion:<chatRunId>`, then navigates to
`/projects/:id/new-run?promoteFrom=<chatRunId>`. The
sessionStorage-vs-URL choice was load-bearing: browsers cap URL length
at ~2KB–32KB depending on stack, and a chat with even a few long
assistant turns can blow past that. The query-string marker is there
just so the wizard knows to consult storage; the storage entry holds
the body. `NewRunWizard.onMounted` reads + removes the entry (one-shot
— a refresh of the wizard URL must not re-populate stale content) and
seeds `promptSource = { promptBody: body }` + `promptMode = 'inline'`.
The wizard's existing preview-then-start flow handles validation and
commit unchanged.

The promotion is **non-destructive** by acceptance criterion: the chat
stays in its current status, no close mutation is fired, no
`markTerminal()` is called. A user can keep talking and promote again
later. Tests cover this explicitly.

### Thread 2 — ADR-49 + ADR-50

**ADR-49** records the chat-mode introduction: `runs.mode = "task" |
"chat"` discriminator, chat mode uses pi's native `--session` resume,
which is the **deliberate opposite** of ADR-20's fresh-context-per-iter
invariant for task mode. Both modes coexist — chat does not relax
ADR-20 in task mode. Rationale: relay's task model is wrong for
conversational queries; pi's native model is right. Sharing the `runs`
table makes infra reuse free (event store, SSE, OTel, worktree
provisioner, pause/resume, MCP framework). The three rejected
alternatives are recorded: separate `chats` table (duplicates half the
code), MCP-only ask tool (no UI surface), configure-existing-task-path
with `max_iters=1` + empty skills (wasteful worktrees +
misclassification).

**ADR-50** records `closed` as a new terminal `runs.status` value,
distinct from `done` (engteam-style normal completion via terminating
sentinel), `cancelled` (user gave up on a task), and `failed` (error).
Written only by `POST /api/runs/{id}/close`. Reachable from `paused`
(the natural chat-mode resting state) and `running` (the operator
clicked Close mid-turn — the close mutation cancels the in-flight
session first). `StatusBadge.vue` renders dim grey + dashed border —
deliberately distinct from `cancelled`'s solid border.

Both ADRs document the **five `_TERMINAL` declarations** that must
stay in sync for any future status work: `src/relay/api/events.py`,
`src/relay/core.py` (multiple cascade/safety-net tuples),
`frontend/src/stores/events.ts` (`TERMINAL_STATUSES`),
`frontend/src/views/RunDetailView.vue` (`TERMINAL`), and
`frontend/src/views/ChatView.vue` (`TERMINAL`, added in W4). Failing to
update one produces silent bugs (SSE streams that never close, events
stores that keep invalidating past run-end). This is the chat-mode
shadow of the 9f dual-list contract.

Verified before writing: the most recent ADR was **ADR-48**, not ADR-41
(CLAUDE.md was stale on the count). New ADRs landed as 49 and 50, not
42 and 43 as the user's hand-off prompt initially suggested.

### Thread 3 — documentation

- `docs/spec.md` §3: added `mode TEXT NOT NULL DEFAULT 'task'` column
  to the `runs` table; widened the `status` value-list to include
  `closed` with the ADR-50 note. §6: added the chat-mode loop branch
  summary (preamble skipped, sentinels not enforced, `session_end`
  writes a synthetic `pause_requested`, `--session` threaded) with an
  explicit "chat mode does not relax ADR-20 in task mode" callout. §9:
  added the chat-detail view (`/chats/:id`) under MVP views with the
  fold rules and the Promote-to-task affordance.
- `docs/orchestrator.md`: new "Chat mode (ADR-49)" section documenting
  the `start_run`/`resume_run`/`run_loop` branches, the skipped skill
  injection, the close endpoint.
- `docs/dashboard.md`: new "Chat mode (ADR-49)" section documenting the
  three sub-components (`ChatHeader` / `ChatTranscript` / `ChatInput`),
  the transcript fold, the W5 Chats tab, the W6 promote-to-task
  sessionStorage handoff.
- `docs/getting-started.md`: brief "Want to chat with a project?"
  section between §4 (first run) and §5 (MCP), pointing at the New
  chat button and Promote-to-task.
- `CLAUDE.md` "Current state" gained a chat-mode-arc paragraph in the
  9a-9g / 14a-14f pattern: terse, technical, every load-bearing
  invariant + every cross-cutting trap. Includes the five-list
  `_TERMINAL` sync rule as a named cross-cutting trap.

### Tests

- `frontend/tests/promotion.spec.ts` — new file. 9 tests covering the
  pure builder: empty conversation, alternating turns, thinking-kind
  drop, empty-answer skip, protocol-event drop, live-iter flush,
  trailing-empty drop, defensive type coercion, project-name
  pass-through.
- `frontend/tests/ChatHeader.spec.ts` — new file. 8 tests covering
  render, project-loading fallback, Close visibility predicate, Close
  click + error path, Promote click emission, Promote visibility on
  closed chats, back-link routing.
- `frontend/tests/ChatView.spec.ts` — replaced the "Promote-to-task
  shows the W6-pending notice" integration test with two new tests:
  navigation + sessionStorage handoff verification, and a
  non-destructive-promotion guard (close mutation must not fire).
- `frontend/tests/NewRunWizard.spec.ts` — added two tests for the
  `?promoteFrom=` deep-link path: prefilled body + one-shot
  consumption, and the missing-storage fallback.

Frontend test count: 438 → 459 (+21). Backend untouched, still 398 +
3 pi-e2e skipped.

## Post-W6 archive hygiene

W6 was the last unit; the arc is complete. Marked
`docs/proposals/chat-mode.md` as `Status: closed 2026-05-30` and moved
it to `docs/archive/2026-05-30-chat-mode-arc.md` via `git mv`. Rewrote
the three inbound links from active docs (`docs/spec.md`,
`docs/decisions.md`, `CLAUDE.md`) to the archive path. The active
`docs/proposals/` listing now contains only the still-active
`run-detail-layout.md` — easy to scan.

## Sharp edges encountered

1. **Test helper `??` collapsed explicit `null`.** First version of
   `ChatHeader.spec.ts` had
   `project: props.project ?? { id: 42, name: 'Alpha' }`. Passing
   `null` triggered the default — there was no way to test the
   loading-state fallback. Fixed by switching to
   `'project' in props ? (props.project ?? null) : default` with an
   explicit type annotation so TypeScript doesn't widen the conditional
   branch to `undefined`. Saving this pattern for any future tests
   that need to distinguish "explicit null" from "argument omitted".
2. **`onPromote` references `eventList` declared further down.** In
   Vue's `<script setup>` body the const is in the same lexical scope;
   the function captures by name at call time. Works, but reads
   strangely. Considered reordering — left as-is because moving the
   computeds above the click handlers would shuffle a chunk of W4 code
   for cosmetics with no behavioral benefit. Worth a note in case the
   pattern spreads.
3. **Wizard `mountAt` helper didn't accept query.** The existing
   `NewRunWizard.spec.ts` helper takes only a projectId; the new
   tests needed to push a route with `?promoteFrom=`. Inlined a
   second push pattern in the two new tests rather than widening the
   helper for two callers — the helper's shape was already a
   "common-path" convenience.

## Verification

- `uv run ruff check .` clean.
- `uv run mypy` clean (40 source files).
- `uv run pytest --no-cov` — 398 passed, 3 skipped (pi-e2e gated).
- `cd frontend && npm run check` — eslint --max-warnings 0 clean,
  vue-tsc clean, 459 vitest tests pass.

End state: the chat-mode arc is complete; the worktree is ready to
merge into `main`. ADR count now 50 (was 48). Backend coverage
unchanged at 95%; W6 was UI + docs only and didn't touch any backend
source.
