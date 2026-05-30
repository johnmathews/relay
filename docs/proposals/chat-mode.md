---
plan: chat-mode
units:
  - id: W1
    title: Schema + run-creation surface for chat mode
  - id: W2
    title: Orchestrator loop branch (auto-pause + pi --session resume)
  - id: W3
    title: New `closed` terminal status + close endpoint
  - id: W4
    title: Frontend ChatView + routing
  - id: W5
    title: Project dashboard "Chat" button + Chats sidebar
  - id: W6
    title: Promote-to-task UI + ADRs + docs
---

# Proposal — chat mode (webui implementation of pi)

**Status:** active. **Last updated:** 2026-05-30. **Supersedes:** none.
**Date:** 2026-05-30.
**Touches:** `docs/spec.md` §3 (schema), §4 (harness), §6 (loop); `src/relay/db/models.py` (`runs.mode`, terminal status `closed`); `src/relay/core.py` (`start_run` mode branch, new `close_chat`, conditional skill injection); `src/relay/orchestrator/loop.py` (auto-pause on `session_end` for chat-mode runs; carry forward `last_session_id`); `src/relay/harness/pi.py` (conditional skill injection); `src/relay/api/runs.py` + `schemas.py` (mode in create payload, close endpoint); `src/relay/mcp/` (additive — `relay__create_chat`, `relay__send_chat_message`, `relay__close_chat`); `frontend/src/views/ChatView.vue` (new); `frontend/src/views/ProjectView.vue` (Chat button + Chats list); `frontend/src/router/index.ts` (route); `docs/spec.md`, `docs/decisions.md` (two new ADRs), `docs/orchestrator.md`, `docs/dashboard.md`.
**Does not touch:** pi protocol (no harness-layer change beyond conditional skill flag), sentinel parser (chat mode emits no sentinels), the chained-iter task loop (task-mode runs are byte-identical), the engineering-team skill (skill is the *opposite* of what chat mode wants), fanout/join (chat mode is single-thread by design), pause-for-review write endpoint (chat mode doesn't review artifacts — pi just edits in worktree).

## Background

Relay's value prop is *fresh context per iter with a compressed handoff* (ADR-20, spec.md §6, CLAUDE.md). That's the right model for the engineering-team workflow — decompose a big plan into work units, run each in a fresh pi session, prevent context rot. It is the wrong model for conversational questions: "where are we at with the plan?", "what phases are still to be implemented?", "explain how the fanout-join watcher resolves the parent". Those are exchanges with continuity, not chained subagent invocations.

User-facing need:

> From the relay webapp, I select a project and click a "chat" button, and I get a normal chat conversation UI with an agent whose context and root directory is the root directory of that project. This isn't for implementing complex plans, but for having a conversation. It's basically a webui implementation of the pi harness.

Pi already supports the multi-turn model natively: spawn `pi -p "<message>" --session <prev_session_id>` per user turn, pi rehydrates the prior conversation. Each turn is a fresh subprocess; pi handles context internally via session storage. Relay's existing harness wires `--session` for crash recovery only (`harness/pi.py:430`, gated by `resume_from`). Chat mode flips that into the primary mechanism: `last_session_id` is intentionally carried forward, turn after turn.

This proposal adds chat mode as a parallel surface — same `runs` table, same event store, same SSE, same worktree provisioner — with a small set of conditional branches that change behaviour when `mode="chat"`. Task runs are byte-identical.

## State of the world (today)

What's already in place that chat mode leans on:

- **`iters.pi_session_id`** (db/models.py:102) — already persisted on every iter for crash recovery. Chat mode reads the *prior* iter's value to thread session resume.
- **`PiSpawner._build_argv`** (harness/pi.py:430) — `--session <id>` already wired, currently only fed `resume_from` on crash. The plumbing chain `start_run → RunContext → loop._drive_iter → spawner.spawn(resume_from=...)` exists end-to-end.
- **`pi_skill_paths`** (config.py:89, ADR-44) — skill injection is already configurable per spawn. Passing an empty list yields a pi process with no engteam discipline.
- **Pause/resume mechanism (14a–14f, ADR-40, ADR-41)** — `paused` run status, `resume_run(answer)` endpoint, pause-for-input event with optional `review_path`. Chat mode reuses the pause state and the resume endpoint; the "answer" is the next user message.
- **Worktree provisioner** (`orchestrator/lifecycle.py:163`) — works unchanged for chat-mode runs.
- **Event store / SSE / OTel / dashboard timeline** — every assistant text, tool call, tool result, usage row already flows through these. Chat mode adds zero event kinds for the in-iter stream.
- **MCP surface** (`mcp/`) — additive tool registration only.

What's missing:

- A way to distinguish chat from task at the run level (no `mode` column on `runs`).
- A loop branch that auto-pauses after `session_end` instead of finalising the run on `no_signal`.
- A flag that says "this iter should pass `resume_from` even though it's not crash recovery".
- A frontend that renders the conversation as alternating chat turns with an input box, not as a task timeline with sentinel banners.

Everything else is reuse.

## The decision space

Three shapes were considered. See the discussion that generated this plan for the full tradeoff table.

**Shape A — Chat as a new run mode** (chosen). One schema column flips a small set of conditional branches. Chats live in the `runs` table alongside tasks but render via a different frontend view. Reuses event store, SSE, OTel, worktree provisioner, pause/resume, MCP framework. Honours the "all writes through `RelayCore`, events are SSoT" invariant (ADR-07, ADR-10) — chat turns are events like any others.

**Shape B — MCP-only ask tool** (rejected). A `relay__ask` tool spawns a one-shot pi subprocess and returns the answer. No DB row, no events, no dashboard. Smallest footprint, but: no history, no UI surface (user wanted dashboard), bypasses the SSoT invariant. Rejected as too narrow given the dashboard requirement and multi-turn need.

**Shape C — Configure existing run path** (rejected). With `RELAY_PI_SKILLS=""` and `max_iters=1`, a task-mode run becomes a one-shot "ask" today. But: every question creates a git branch, the "no sentinel" termination shows as `failed`, and there's no multi-turn surface. The UX cost is high relative to the implementation savings.

Shape A is structurally right because chat mode is a real second product surface — it deserves the modest schema cost. Shape B may still be worth doing later as a thin wrapper over Shape A (one MCP tool that creates a chat-mode run programmatically); not in this plan.

## Decisions & tradeoffs

These are the cross-cutting calls that shape the work units. Decisions live up top because they survive long after the execution sequence ages out.

1. **Chat = run with `mode="chat"`.** A column on `runs`, not a separate table. Sharing the runs table is what makes everything (event store, SSE, OTel, worktree provisioner, pause/resume, MCP framework) automatically reusable. The cost is one schema column and a few conditional branches; the alternative (separate `chats` table) would duplicate half the codebase. Default value is `"task"` so every existing row stays correct.

2. **Pi session resume IS used between iters in chat mode.** ADR-20's "fresh context per iter" invariant is intentionally inverted here. The new ADR (numbered below) records this as a *mode-specific* invariant inversion, not a relaxation of ADR-20 in task mode. The two modes co-exist with opposite invariants.

3. **Each user turn = one iter.** The pause/resume mechanism (14a–14f) handles "wait for next user message" via the existing `paused` state. After pi's `session_end`, the loop writes a synthetic `pause-for-input` event with no `question`, no `review_paths`, and signals the run as `paused`. `resume_run(run_id, answer=<next-message>)` triggers the next iter exactly as it does for engteam pauses today.

4. **Skill injection skipped entirely in chat mode.** Pi gets no `--skill` arguments — neither bundled engteam nor any project-local skill. The whole point of chat mode is unstructured conversation; a skill that nudges the agent toward phase sentinels actively works against it. (Note: pi's own auto-discovery of `<cwd>/.pi/skills/` still applies; that's pi's policy, not relay's.)

5. **Worktree provisioned same as task mode** (per user direction). Chat-mode pi has full write access to the worktree. This means a chat can casually evolve into "do one small edit" without forcing a promote-to-task ceremony for trivial changes. The tradeoff: pi may edit files mid-chat without the user explicitly asking — accepted, because the promote-to-task path exists for anything bigger.

6. **New terminal status `closed`** (not reuse `cancelled`). Chats that the user voluntarily ends are not failures or cancellations — `closed` is the natural terminal state. This costs three small additions (`_TERMINAL` constants in `api/events.py`, `frontend/src/stores/events.ts`, `frontend/src/views/RunDetailView.vue`) matching the 9a precedent for `awaiting_children`. Forces a `StatusBadge.vue` variant.

7. **Manual promote-to-task only** (per user direction). No slash command, no heuristic auto-promote. A "Promote to task" button opens the new-run form with the chat transcript pre-embedded in the prompt body. The user edits and submits. Clean handoff, explicit user gate.

8. **Pi session_id is the resume key, not a synthesised relay id.** Pi already mints a `session_id` per spawn (`harness/pi.py:319`); the harness already persists it on the iter (`db/models.py:102`). Chat mode reads `iters[-1].pi_session_id` to thread `resume_from` into the next spawn. No new identifier types.

9. **Chats and tasks share the `runs` table but are visually segregated in the frontend.** Mixing them in one list would clutter both workflows. A separate Chats sidebar at the project view + a separate `/chats/:id` route (not `/runs/:id`) keeps the two surfaces mentally distinct while the data model stays unified.

10. **No fanout from chat mode.** A chat-mode iter that emits `[[engteam:fanout]]` is treated as a parse error and ignored. Fanout requires the engteam skill's structured JSON payload (ADR-35), and chat mode injects no skill. If someone wants parallel exploration from a chat, they promote it to a task. This is an enforcement-by-absence, not a new guard.

11. **Token cost is pi's problem, not relay's.** Long chats accumulate context inside pi's session storage; pi handles compaction internally. Relay doesn't reconstruct the transcript per turn — `--session <id>` does the work. Long-running chats may eventually hit pi's own context limits; when they do, the answer is "promote and start fresh", which the manual promote-to-task path already enables.

## Non-goals

- **No multi-user / auth / ACLs.** Single-user, localhost MVP (ADR-12) still applies. Chat mode is per-user state on a single-user system.
- **No chat-level persistence beyond what runs already get.** No "rename chat", no "star chat", no "search chats by content" — those are obvious follow-ons but not in this plan.
- **No fanout, no subagents, no skill injection in chat mode.** Stated above; calling out explicitly to head off scope creep.
- **No "ask" CLI subcommand** (`relay ask "..."`). The user's framing was dashboard-only; CLI is a separate proposal if it becomes useful.
- **No native MCP chat tool in this plan.** A `relay__create_chat` etc. MCP surface (W1) is included because it's a one-line addition over the new REST endpoint; full conversational MCP usage (back-and-forth via MCP) is a follow-on.
- **No "summarise and continue" compaction tool in the UI.** Pi compacts its own context.
- **No automatic chat → task escalation.** Manual promote-to-task only.
- **No streaming user input** (typing-while-pi-responds). User input is gated on pi having finished the current turn (the `paused` state). A future enhancement could allow interrupt-by-new-message but adds significant complexity for marginal benefit.

## Work units

Ordering rationale: foundation-first (schema + run-creation surface gate everything else), then mechanism (loop + terminal status), then surface (frontend), then UX polish (sidebar + promote button + ADRs). Within W1–W2, backend lands before frontend so the API is stable when ChatView is wired.

### W1 — Schema + run-creation surface for chat mode

- **ID:** W1
- **Title:** Schema + run-creation surface for chat mode
- **Priority:** High
- **Risk:** Medium — schema column + new REST/MCP entry points, but additive only; existing task runs default to `mode="task"` and are byte-identical.
- **Size:** M
- **Changes:**
  - `src/relay/db/models.py`: add `Run.mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="task")`. Constrain to literal `"task" | "chat"` at the Python boundary (Pydantic schema + helper). No CHECK constraint in SQLite to avoid migration ceremony (consistent with hand-rolled `create_all` + ADR-17).
  - `src/relay/core.py`: extend `start_run(...)` signature with `mode: Literal["task", "chat"] = "task"`. New convenience method `start_chat(project_id) -> str` that calls `start_run(project_id, prompt_body="", mode="chat", max_iters=settings.chat_max_iters)`. Chat-mode runs are created with an empty initial `prompt_body` — pi runs no first-turn prompt; the run sits in `paused` immediately so the user can type the first message. (See W2 for the loop branch that produces this initial pause.)
  - `src/relay/config.py`: add `chat_max_iters: int = 200` to `Settings`, env override `RELAY_CHAT_MAX_ITERS`. High enough that no real chat hits it; serves as a safety cap.
  - `src/relay/api/schemas.py`: extend `CreateRunRequest` with `mode: Literal["task","chat"] = "task"`. Add `ChatRead` schema (subset of `RunRead` — most fields the same, included for typed-client clarity).
  - `src/relay/api/runs.py`: extend `POST /api/runs` to accept `mode`. Validate that `mode="chat"` requests omit `prompt_body` (or accept empty). Add `POST /api/projects/{project_id}/chats` as a thin convenience endpoint that creates a chat-mode run with empty body. Add `GET /api/projects/{project_id}/chats` (filtered list — `runs WHERE mode='chat' AND project_id=?`).
  - `src/relay/mcp/`: register `relay__create_chat(project_id) -> chat_id` and `relay__list_chats(project_id) -> [...]` as thin `RelayCore.start_chat` / `list_runs(mode="chat")` adapters. ADR-27 lifespan footgun unchanged.
- **Test impact:**
  - New: `tests/db/test_models.py::test_runs_mode_defaults_to_task` (existing rows + new task-mode rows). `tests/orchestrator/test_core.py::test_start_chat_creates_chat_mode_run_no_body`. `tests/api/test_runs.py::test_create_chat_run_via_rest`, `::test_list_chats_filters_by_mode`. `tests/mcp/test_mcp_tools.py::test_relay_create_chat`.
  - Updated: `tests/orchestrator/test_core.py::test_start_run_*` — verify `mode="task"` is set on every existing-shape call (regression guard). `tests/api/test_runs.py` — extend the create payload fixture to confirm default `mode="task"` round-trips.
  - Read these before changing: `tests/orchestrator/test_core.py` (full file — many `start_run` callers), `tests/api/test_runs.py`.
- **Reversibility:** Pure additive code change + schema column with a default. Revert commit drops the column on a fresh DB; existing prod DBs retain the column harmlessly. No data migration.
- **Dependencies:** None — foundation unit.
- **Acceptance criteria:**
  - `POST /api/runs {project_id, mode:"chat"}` creates a run with `mode="chat"`, `prompt_body=""`, status `paused` (after W2 lands; before W2, status will transiently be `pending` — that's fine for this unit's tests, which seed the row directly).
  - `relay__create_chat` MCP tool returns a `chat_id` that subsequently shows up in `relay__list_chats` and NOT in `relay__list_runs(mode="task")` (or whatever the filter is).
  - All existing tests pass with no modification beyond the regression guard above.
  - `mypy --strict` clean, `ruff check` clean.

### W2 — Orchestrator loop branch (auto-pause + pi --session resume)

- **ID:** W2
- **Title:** Orchestrator loop branch (auto-pause + pi --session resume)
- **Priority:** High
- **Risk:** High — touches the load-bearing chained-iter loop. A bug here can corrupt task-mode runs. Mitigation: every chat-mode branch is guarded by an explicit `ctx.mode == "chat"` check; task mode goes through the existing code path byte-for-byte. Comprehensive regression tests on task-mode behaviour are mandatory.
- **Size:** M
- **Changes:**
  - `src/relay/orchestrator/loop.py`: in `run_loop` (the chained-iter driver), branch on `ctx.mode`:
    - Task mode (current): `last_session_id = None` between iters (line 286 invariant preserved).
    - Chat mode: `last_session_id = previous_iter.pi_session_id` between iters. Carries forward whenever the previous iter persisted a non-null session_id.
  - `src/relay/orchestrator/loop.py`: in `_finish_iter` / terminal-status handling, branch on `ctx.mode`:
    - Task mode (current): `no_signal` → run finalises as `failed` (or whatever current behaviour is); sentinel-driven termination unchanged.
    - Chat mode: any iter that ends with `stop_reason in {"end_turn", "session_end"}` (i.e. pi naturally finished a turn) writes a synthetic `pause-for-input` event with `signal_args = {"id": f"chat-{run_id}-{iter_seq}", "question": null, "review_paths": []}` and transitions the run to `paused`. The `harness_session_ended` event (ADR-39) is still written first.
  - `src/relay/orchestrator/loop.py`: chat-mode iters do NOT enforce sentinel presence. A chat-mode iter that *does* emit a sentinel (e.g. pi got confused) is logged at WARNING and treated as `no_signal`.
  - `src/relay/orchestrator/preamble.py`: chat mode emits NO `RELAY_*` preamble. Pi receives the user's message verbatim as `prompt_body`. The current preamble injection (RUN_DIR, PHASE, prior-iter handoff, etc.) is task-mode only.
  - `src/relay/harness/pi.py`: `PiSpawner._build_argv` already supports `resume_from`; no change needed. Add a `skill_paths: list[Path] | None = None` override on `spawn(...)` that, when non-None, replaces `self._settings.pi_skill_paths`. Chat mode passes `[]` to suppress skill injection.
  - `src/relay/core.py`: `_run` (line 754) threads `ctx.mode` into the loop. `resume_run` (line 1226) for chat-mode runs uses the user's resume answer as the next iter's `prompt_body` directly (no preamble wrapping). For chat-mode runs, the next iter's `RunContext` carries `resume_from = previous_iter.pi_session_id`.
  - `src/relay/core.py`: `start_run(mode="chat")` enqueues a chat-mode run that immediately enters the loop, which immediately writes a synthetic `pause-for-input` event because there's no first-turn user message — the run lands in `paused` after a single trivial iter pass with no pi spawn. (Alternative: special-case `start_run` to write the pause event directly without entering the loop. Chosen alternative TBD during implementation — both work; the trivial-iter path is more uniform, the direct-write path is faster.)
- **Test impact:**
  - New: `tests/orchestrator/test_loop.py::test_chat_mode_auto_pauses_on_session_end`, `::test_chat_mode_carries_pi_session_id_forward`, `::test_chat_mode_skips_preamble`, `::test_chat_mode_skips_sentinel_enforcement`, `::test_chat_mode_no_skill_injection_in_spawn`. `tests/orchestrator/test_resume.py::test_resume_chat_uses_user_message_as_prompt_body`. `tests/orchestrator/test_resume.py::test_chat_iter_n_threads_session_id_from_iter_n_minus_1`.
  - Updated: every existing test in `tests/orchestrator/test_loop.py` that asserts on `last_session_id=None` — extend with a comment confirming the assertion is *task-mode-specific*. Read every test in `tests/orchestrator/test_loop.py` and `tests/orchestrator/test_resume.py` before writing code; this is the highest-risk change in the plan.
  - Read these before changing: `src/relay/orchestrator/loop.py` (full file), `src/relay/orchestrator/preamble.py`, `tests/orchestrator/test_loop.py`, `tests/orchestrator/test_resume.py`, `tests/orchestrator/test_core.py`.
- **Reversibility:** Pure code change behind a mode flag; revert commit removes the branches. No data migration. If a chat-mode run is mid-flight at the time of revert, it will fail to resume (the chat resume path uses `mode="chat"`); document the procedure for finishing in-flight chats before deploying a revert.
- **Dependencies:** **W1** (needs the `mode` column on `runs`).
- **Acceptance criteria:**
  - A new chat-mode run lands in `paused` status with no pi spawn occurring for the synthetic initial pause.
  - `resume_run(chat_id, "hello")` spawns pi with `-p "hello"` and no `--session` (first turn has no prior session_id), and no `--skill`. Pi's `assistant_text` events flow into the event store; the run pauses again on `session_end`.
  - A second `resume_run(chat_id, "follow-up")` spawns pi with `-p "follow-up" --session <session_id_from_first_iter>`. Pi receives the prior conversation via its own session storage.
  - All existing task-mode loop tests pass unchanged. (Regression gate is critical.)
  - `harness_session_ended` (ADR-39) and `iter_ended` events still written on every iter close, both task and chat modes.

### W3 — New `closed` terminal status + close endpoint

- **ID:** W3
- **Title:** New `closed` terminal status + close endpoint
- **Priority:** High
- **Risk:** Medium — touches the `_TERMINAL` set in three places (api/events, frontend events store, frontend RunDetailView). The 9a precedent (`awaiting_children`) is a clear template; follow it exactly.
- **Size:** S
- **Changes:**
  - `src/relay/db/models.py`: extend the `RunStatus` literal type to include `"closed"`. No schema change — `status` is already `Text`.
  - `src/relay/core.py`: new method `close_chat(run_id: str) -> None`. Acquires `_enqueue_lock`, verifies `run.mode == "chat"` and `run.status in {"paused", "running"}` (cancel-during-pi-spawn is supported by reusing the existing cancel path), flips status to `closed`, writes `run_ended` event with `{status: "closed", summary: "user closed chat"}`. For `running` chats, the `cancel_run` path's session-cancel logic is invoked; the only difference from `cancel_run` is the final status (`closed` vs `cancelled`).
  - `src/relay/api/runs.py`: `POST /api/runs/{id}/close` endpoint. 409 if `mode != "chat"` (close is chat-only). 409 if status is already terminal. Otherwise calls `RelayCore.close_chat`.
  - `src/relay/mcp/`: `relay__close_chat(chat_id)` adapter.
  - `src/relay/api/events.py`: add `"closed"` to `_TERMINAL` set.
  - `frontend/src/stores/events.ts`: add `"closed"` to `_TERMINAL` set.
  - `frontend/src/views/RunDetailView.vue`: add `"closed"` to `_TERMINAL` set.
  - `frontend/src/components/StatusBadge.vue`: add a `closed` variant (neutral grey, distinct from `done` green / `cancelled` red / `failed` red).
- **Test impact:**
  - New: `tests/orchestrator/test_core.py::test_close_chat_paused`, `::test_close_chat_running_cancels_pi`, `::test_close_chat_rejects_task_mode`, `::test_close_chat_idempotent_on_terminal`. `tests/api/test_runs.py::test_close_endpoint_409_on_task_mode`. `tests/mcp/test_mcp_tools.py::test_relay_close_chat`.
  - Updated: any test that exhaustively enumerates the `RunStatus` literal — extend.
  - Frontend: `frontend/tests/views/RunDetailView.spec.ts` — extend the `_TERMINAL` assertion. New `frontend/tests/components/StatusBadge.spec.ts::renders closed variant`.
  - Read these before changing: search `_TERMINAL` codebase-wide first; the 9a `awaiting_children` change is the template, search history for that.
- **Reversibility:** Pure additive code change. Revert commit removes the status from the literal type; if a `closed` row exists in prod, it will deserialise as an unknown status string — acceptable transient state, easy to fix manually (`UPDATE runs SET status='cancelled' WHERE status='closed'`).
- **Dependencies:** **W1** (mode column needed for the chat-only check), **W2** (chat runs must exist to be closed).
- **Acceptance criteria:**
  - `POST /api/runs/{paused-chat-id}/close` returns 200, flips status to `closed`, emits `run_ended` event.
  - `POST /api/runs/{task-id}/close` returns 409 with a clear error code.
  - Closing a `running` chat (pi mid-response) cancels the pi process and writes `run_ended` with `status:"closed"`, not `status:"cancelled"`.
  - All four `_TERMINAL` declarations (backend events, frontend store, frontend RunDetailView) include `"closed"`.

### W4 — Frontend ChatView + routing

- **ID:** W4
- **Title:** Frontend ChatView + routing
- **Priority:** High
- **Risk:** Medium — new view, but reuses existing components (tool cards, assistant text rendering, SSE event store). The risk is mostly UX correctness, not corruption.
- **Size:** L — three sub-components plus routing plus integration tests. Flag for possible split if it grows during implementation. Possible split point: extract `ChatTranscript.vue` and `ChatInput.vue` early; ship them as W4a, then ship `ChatView.vue` orchestration as W4b. Keeping as one unit unless implementation reveals natural seams.
- **Changes:**
  - `frontend/src/views/ChatView.vue` (new): alternating user/assistant transcript, scrolled to bottom. Input box at the bottom with a Send button. SSE-driven live updates via the existing events store (`stores/events.ts` — the chat view subscribes to the same `run:<id>` channel).
  - `frontend/src/components/ChatTranscript.vue` (new): renders the conversation. Each user turn = the resume-answer text from the corresponding iter's `signal_args.answer` (or wherever resume answers are persisted — verify by reading W2's resume path). Each assistant turn = the iter's `assistant_text` events concatenated, with tool cards interleaved chronologically. Reuses `MarkdownRender`, `ToolCard`, and `AssistantTextDelta` (ADR-46) components verbatim.
  - `frontend/src/components/ChatInput.vue` (new): textarea + Send button. Enter submits (Shift+Enter newline). Disabled while the run is `running`. Calls `useResumeRunMutation` with the textarea content as the answer; clears on success.
  - `frontend/src/components/ChatHeader.vue` (new, small): shows project name, "Close chat" button (calls W3's close endpoint), "Promote to task" button (deferred to W6 — stub in W4).
  - `frontend/src/router/index.ts`: new route `/chats/:id` → `ChatView`. Existing `/runs/:id` route is untouched; if a chat-mode run is opened via `/runs/:id`, redirect to `/chats/:id` (one-line guard in `RunDetailView.vue`'s setup).
  - `frontend/src/api/queries.ts` or equivalent: new `useChatRunQuery(chatId)` Pinia Colada hook (or reuse `useRunQuery` — chats are runs). New `useCloseChatMutation(chatId)`.
  - Heartbeat (ADR-45) and assistant_delta (ADR-46) handling: ChatView reuses the events store's pending-map for live streaming exactly as `TimelinePane.vue` does. No new SSE plumbing.
- **Test impact:**
  - New: `frontend/tests/views/ChatView.spec.ts` — renders an existing chat (mocked events), submits a message, shows new assistant text on SSE delivery. `frontend/tests/components/ChatTranscript.spec.ts`, `::ChatInput.spec.ts`. `frontend/tests/components/ChatInput.spec.ts::enter submits / shift+enter newline / disabled while running`.
  - Read these before changing: `frontend/src/views/RunDetailView.vue` (full file — pattern source for the SSE-driven view), `frontend/src/components/TimelinePane.vue` (assistant text + tool card rendering), `frontend/src/stores/events.ts` (subscription model).
- **Reversibility:** Pure code change in `frontend/`. Revert commit removes the route and views. No persistent state.
- **Dependencies:** **W2** (the resume-message-as-prompt-body mechanism must exist for the input box to do anything), **W3** (close endpoint must exist for the close button). The route can technically land before W3 with the close button stubbed; cleaner to land after.
- **Acceptance criteria:**
  - Navigating to `/chats/:id` for an existing chat-mode run renders the conversation correctly.
  - Submitting a message in the input box triggers `resume_run`, the run transitions `paused → running`, pi's response streams in via SSE (deltas show as ADR-46 pending rows, then collapse into the canonical assistant_text on `turn_end`), the run transitions back to `paused`, and the input re-enables.
  - The "Close chat" button calls `POST /api/runs/{id}/close` and the view transitions to a terminal display (input disabled, status badge shows `closed`).
  - `/runs/:id` for a chat-mode run redirects to `/chats/:id`.
  - `npm run check` clean.

### W5 — Project dashboard "Chat" button + Chats sidebar

- **ID:** W5
- **Title:** Project dashboard "Chat" button + Chats sidebar
- **Priority:** Medium
- **Risk:** Low — pure UI addition on an existing view.
- **Size:** S
- **Changes:**
  - `frontend/src/views/ProjectView.vue`: add a "New chat" button next to the existing "New run" button. Calls `POST /api/projects/{id}/chats` and navigates to `/chats/<new_id>`.
  - `frontend/src/views/ProjectView.vue`: add a "Chats" list section below the existing Runs list (or in a sidebar — TBD by frontend layout review). Lists chats for the project sorted by `updated_at` descending. Each row shows: short-id · last-message-preview (first 60 chars of the most recent assistant text) · status badge · timestamp. Clicking a row navigates to `/chats/:id`.
  - `frontend/src/api/queries.ts`: `useProjectChatsQuery(projectId)` hook (Pinia Colada, invalidates on `run_started` / `run_ended` events from any chat-mode run for this project — or simpler, on a 30s poll).
- **Test impact:**
  - New: `frontend/tests/views/ProjectView.spec.ts::renders chats list separately from runs list`, `::new chat button creates and navigates`. `frontend/tests/components/ChatListRow.spec.ts` (if extracted as a component).
  - Read these before changing: `frontend/src/views/ProjectView.vue` (full file — existing layout to match).
- **Reversibility:** Pure UI revert.
- **Dependencies:** **W1** (needs `relay__list_chats` / equivalent REST endpoint), **W4** (needs `/chats/:id` route to navigate to).
- **Acceptance criteria:**
  - Project view shows two visually separate sections: Runs and Chats.
  - Clicking "New chat" creates a chat-mode run and navigates to its chat view with focus in the input box.
  - Closed chats appear in the Chats list with a `closed` badge.

### W6 — Promote-to-task UI + ADRs + docs

- **ID:** W6
- **Title:** Promote-to-task UI + ADRs + docs
- **Priority:** Medium
- **Risk:** Low — UI prefill + documentation.
- **Size:** M
- **Changes:**
  - `frontend/src/components/ChatHeader.vue`: wire the "Promote to task" button (stubbed in W4). On click, navigates to the existing new-run form (`/projects/:id/new-run` or wherever) with the prompt body pre-filled. The transcript format is:

    ```
    Context: this task originated from a chat conversation in project <name>.

    --- Conversation ---
    User: <message 1>
    Assistant: <assistant text 1 — concatenated AssistantText events for iter 1>
    User: <message 2>
    Assistant: <...>
    --- End conversation ---

    Continue with the work the chat was building toward.
    ```

    User can edit before submitting. The chat itself stays open (the user might want to keep talking and promote again later).
  - New ADR in `docs/decisions.md`: **ADR-NN — chat mode: a webui for pi alongside the chained-iter task model.** Records:
    - Decision: introduce `runs.mode = "task" | "chat"`; chat mode uses pi's native session-resume (`--session`) across iters, opposite of ADR-20's fresh-context-per-iter invariant for task mode.
    - Rationale: relay's task model is wrong for conversational queries; pi's native model is right. Sharing the runs table makes infra reuse free.
    - Alternatives rejected: separate `chats` table (duplicates half the code), MCP-only ask tool (no UI surface, no history), configure existing run path (wasteful worktrees, mis-classified status).
    - Cost: one schema column, ~3 conditional branches in the loop, ~3 new frontend views.
  - New ADR in `docs/decisions.md`: **ADR-NN+1 — `closed` as a new terminal run status.** Records that `closed` is the chat-mode voluntary-end terminal status, distinct from `done` (engteam-style normal completion), `cancelled` (user-cancelled task), and `failed` (error). Notes the three `_TERMINAL` declarations that must stay in sync.
  - Update `docs/spec.md`: §3 schema (add `mode` column to `runs` table definition), §6 loop (mode-branching summary, with task mode noted as ADR-20 default and chat mode as ADR-NN inversion), §11 dashboard (Chats sidebar reference).
  - Update `docs/orchestrator.md`: chat-mode loop branch documented.
  - Update `docs/dashboard.md`: ChatView documented.
  - Update `docs/getting-started.md`: brief "Want to chat with a project?" section.
  - Update `CLAUDE.md` "Current state" section to note chat mode shipped (after acceptance).
- **Test impact:**
  - New: `frontend/tests/components/ChatHeader.spec.ts::promote button prefills new-run form`. Verify the transcript-formatting helper as a pure unit test (`frontend/tests/utils/buildPromotionPrompt.spec.ts`).
  - Read these before changing: the new-run form component; how `prompt_body` is wired into its initial state (verify a query-param-driven prefill is possible without a backend change).
- **Reversibility:** Pure code change.
- **Dependencies:** **W4** (button placement), **W5** (chats list to test from), and all prior units (ADRs document the shipped behaviour).
- **Acceptance criteria:**
  - Clicking "Promote to task" from an open chat opens the new-run form with the transcript pre-filled and editable.
  - Submitting the new run creates a `mode="task"` run that proceeds through the engteam workflow normally.
  - The chat that spawned the promotion stays in its current state (`paused` or `closed`) — promotion is non-destructive.
  - All new ADRs append-only (ADR log rules unchanged).
  - `docs/spec.md`, `docs/orchestrator.md`, `docs/dashboard.md` reflect the shipped behaviour.

## Open questions

1. **Where to persist resume answers** (user messages) such that ChatView can render them as user turns. The 14a `pause-for-input` mechanism writes `signal_args.question` to the paused iter but the *answer* (the resume input) is currently passed as the next iter's `prompt_body` and not separately recorded as a "user said X" event. For chat mode, ChatView needs to render user messages as their own visual turns. Options:
   - (a) Add a new `user_message` event kind emitted by `resume_run` for chat-mode runs, payload `{text}`. Cleanest. Adds an event kind.
   - (b) Reuse the next iter's `iter_started` event with an extended payload field `{user_message: "..."}` for chat-mode iters. Avoids a new event kind.
   - (c) Read the resume answer from the prior paused iter's `resume_answer` field (would need to be added).

   Lean: (a) — explicit, queryable, mirrors the assistant_text shape. Resolve during W2 implementation; commit to (a) in the resulting ADR if no surprises.

2. **What does ChatView show on the *first* turn before any user message?** Empty transcript + focused input box. The chat-mode run starts in `paused` with no iters at all. Confirm this is acceptable UX; no tests yet exist for "zero-iter chat".

3. **Token / cost visibility.** Task-mode runs surface per-iter usage via `UsageRow.vue` (ADR-39, ADR-29). Should chat mode surface per-turn token cost inline? Likely yes (low marginal cost — `UsageRow` is reusable as-is), but worth confirming with the user. Defer the decision to W4 implementation; default behaviour: show usage per turn.

4. **What happens if pi crashes mid-chat?** The existing orphan-sweep on startup (ADR-31) will finalise the run as `failed`. For chat mode, the user would prefer it to land in `paused` so they can retry the last message. Two options:
   - (a) Special-case orphan-sweep for chat-mode runs: finalise the *iter* as failed but flip the *run* back to `paused`.
   - (b) Accept `failed` and add a "Retry last message" button that creates a new chat seeded with the prior transcript.

   Lean: (a) — better UX, small additional logic in `_recover_orphans`. Confirm during W2 implementation. If (a) proves complex, defer to a follow-on plan and ship (b)'s "Retry" button.

5. **Multiple concurrent chats per project — concurrency cap?** Today, runs share the supervisor's concurrency limits (per-project, per-tenant). Chats are runs and inherit the same limits. Verify this is acceptable; if a user has 5 chats open and 5 task runs, they're all competing for the same slot. Probably fine for MVP; flag for follow-on if real users hit it.

6. **Skill auto-discovery interaction.** Pi auto-discovers `<cwd>/.pi/skills/` and `~/.pi/agent/skills/` skills regardless of `--skill`. Chat mode runs in the worktree (a child of project_root), so pi will still pick up any project-local skills. Is this desired? Probably yes — project conventions should apply to chats — but flagging in case the user wants chat to be truly skill-free. Default: leave pi's auto-discovery alone.

## Kill criteria

- **Pi removes or changes `--session` semantics in a way that breaks resume-across-spawns.** Chat mode depends on this being pi's native mechanism. If pi v0.75+ removes it (extremely unlikely — it's a load-bearing pi feature), the whole plan is dead and we'd need a different approach.
- **`PI_AGENT_SDK=1` stops gating context-window-friendly behaviour and pi sessions become uneconomic at long conversations.** Currently fine; if it changes, the plan still works but the UX suffers and we'd want a context-compaction story.
- **A separate "chat with a project" surface emerges from upstream pi or a sibling tool that obsoletes this work.** Reassess.

## Test plan / acceptance

End-to-end test on a real project (live `PI_INTEGRATION=1`, journal-attested per ADR-30):

1. Open relay dashboard, select a project, click "New chat". Verify navigation to `/chats/<id>` and empty transcript with focused input.
2. Type "where are we at with the chat-mode plan?" → submit. Verify pi spawns with `-p "..."` no `--session` no `--skill`. Watch SSE deltas render. Verify assistant turn renders, status returns to `paused`, input re-enables.
3. Type follow-up "what work units have hard dependencies?" → submit. Verify pi spawns with `-p "..." --session <prior_id>`. Verify pi answers contextually (referencing prior message — proves session resume works).
4. Click "Promote to task" → verify new-run form opens with full transcript prefilled.
5. Edit prompt slightly, submit. Verify a new mode="task" run starts, engteam skill loaded, sentinels emitted normally.
6. Return to original chat, type another message, verify chat is still live. Click "Close chat" → verify status flips to `closed`, input disabled.
7. Restart relay server mid-chat (kill -9 + restart). Verify orphan-sweep handles in-flight chat correctly per OQ-4 resolution.

Full gate per ADR-30: Python `ruff check .` + `mypy` + `pytest` clean; frontend `npm run check` clean (eslint + vue-tsc + vitest, max-warnings 0). Test counts after this plan ships: roughly **+20 backend tests** (W1 +6, W2 +8, W3 +6), **+15 frontend tests** (W4 +10, W5 +3, W6 +2). No drop in backend coverage from current 95%.
