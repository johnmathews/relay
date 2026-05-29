# Phase 4: Wrap up

> Loaded when the preamble specifies `RELAY_PHASE: wrap-up`, or when the
> last work unit of the improvement plan has emitted `unit-done` and no
> open units remain.

## Emit phase-start immediately

Before any other action in this phase, emit at column 0:

    [[engteam:phase-start phase="wrap-up"]]

## Closing sentinel

Phase 4 always ends with exactly one of `handoff`, `done`, or
`pause-for-input`. See `../references/sentinels.md` for the full contract.

When the plan has no further work units to dispatch, emit `done`. When
work remains in the next session, emit `handoff` with a marker-bracketed
next-session prompt (`[[engteam:prompt-start]]` ... `[[engteam:prompt-end]]`)
immediately before it. See `../references/sentinels.md` for the prompt
marker contract.

---

After all engineering team phases are complete, this phase handles the
quality gate, committing, merging, and CI verification. Do not ask the
user whether to run this phase — it runs automatically as the final step
of every engineering team invocation.

> **v2 note: there is no `/done` or `/merge-push` slash-skill.** Those
> are Claude Code skills; relay runs the engineering-team skill under
> the **pi** harness, where they do not exist. v1's Phase 4 delegated the
> quality gate to `/done` and the merge to `/merge-push`. In v2 you
> perform their steps **inline**, as described below. The intent is
> unchanged — the unit-by-unit dev loop skips lint, type-check, and a
> security pass, so Phase 4 must run them explicitly before anything
> merges.

### Step 1: Quality gate (mandatory, inline)

Run, in order, and do not proceed to merge until each is green:

1. **Sanity:** `git status` — confirm every Phase-3 change is committed
   (commit per work unit during Phase 3, or commit the cumulative work
   now with a clear message). The working tree must be clean.
2. **CI/CD config:** If the repo has `.github/workflows/`, read the
   workflow(s) and confirm your changes don't break them (new deps in
   the lockfile, changed entrypoints, etc.). If the repo has a
   `Dockerfile`/`docker-compose` but no GHCR-publish workflow, that was
   a Phase-1 finding — it should already be a work unit, not a
   surprise here.
3. **Docs:** Confirm `/docs` and `/journal` reflect the changes (global
   policy: every project keeps `/docs` accurate and a dated `/journal`
   entry per change). Doc updates were part of each work unit's doc →
   test → code order; verify nothing was missed.
4. **Tests:** Run the **full** test suite and generate the final HTML
   coverage report (the one time it's generated — see
   `phase-3-development.md`). All tests green; record the final coverage
   % against the Phase-1 baseline.
5. **Security review:** Do a deliberate security pass over the diff
   (`git diff main...HEAD`): injection, auth, secrets committed, unsafe
   deserialization, path traversal, info leakage in errors. This pass
   is not run by the unit loop — it is the reason this step is
   mandatory. Report findings; fix Critical/High before merge or pause
   for the user if the fix is non-trivial.
6. **Lint + types:** Run the project's linter and type-checker (detect
   from Phase 1 — e.g. for a `uv` Python project: `uv run ruff check .`
   and `uv run mypy`; for JS/TS: the project's `lint`/`typecheck`
   scripts). Zero warnings/errors before merge.
7. **Journal:** Write the dated journal entry now (Phase 3 deliberately
   did not — see `phase-3-development.md`). Filename
   `journal/YYMMDD-descriptive-name.md`. Document: what changed and why,
   key decisions, issues discovered that weren't in the plan, test
   coverage delta, tooling changes, remaining concerns / follow-ups.

**"Don't push" does not mean "skip the gate".** If the user has told you
not to push (e.g. they want to review the merge first), that constrains
only the final push step (Step 3). Every gate step above still runs. The
same applies to "don't merge" / "don't open a PR" — each constrains a
single step, not the whole phase. Do not infer broader permissions from
narrow restrictions.

Adapt the gate to the project type:
- **Non-coding projects** (documentation repos, config collections,
  Ansible playbooks, notes): skip the test step if no executable code
  exists; lint only if a relevant linter exists.
- **Mixed projects** (some code + mostly config/docs): apply test/lint
  only to the executable portions.

After the gate, verify:
1. `git status` shows a clean working tree (zero uncommitted changes).
2. `git log --oneline main..HEAD` shows clean, well-described commits.
3. The journal entry exists on disk under `journal/`.

### Step 2: Merge & cleanup

relay provisioned the per-run worktree on branch `relay/<run_id>`
(ADR-13). Integrate it:

1. Confirm the branch state: all work committed, gate green, on
   `relay/<run_id>` inside `.relay/worktrees/<run_id>/`.
2. Fast-forward (or `--no-ff` if the user prefers a merge commit) the
   per-run branch into `main`. Prefer FF for a single mergeable unit.
3. **Ask for explicit confirmation before pushing.** Single-user
   localhost MVP defaults to *merge locally, do not push* unless the
   user said to push. Pushing is the one outward-facing, hard-to-revert
   step — confirm first.
4. Worktree cleanup (`git worktree remove`, branch delete) is
   best-effort and may be left to relay / the user; do not force-remove
   a worktree that still has uncommitted state.

If there was **no worktree** (relay degraded provisioning — non-git or
ad-hoc dir), there is nothing to merge: the work is already on the
working branch. Skip to Step 4 (and see "Simple Wrap-Up" below).

### Step 3: Monitor CI

Only if you pushed. **Always watch the GitHub Actions CI workflow** to
confirm it passes:

1. Run `gh run watch` to monitor the triggered workflow.
2. If CI passes, proceed to the summary.
3. If CI fails:
   a. Read the failure logs with `gh run view <id> --log-failed`.
   b. Diagnose the root cause.
   c. Write a failing test that reproduces the issue (when applicable).
   d. Fix the code, run the full test suite locally, commit, and push.
   e. Watch CI again. Repeat up to 3 times. If still failing, pause for the user.

Do not consider the work complete until CI is green. This is not optional
(when a push happened).

### Step 4: Summary

Present a brief summary to the user:
- What was evaluated/planned/implemented
- The merge commit hash (from `git log -1 --oneline`)
- Coverage delta vs. the Phase-1 baseline
- CI status (passed / failed + what was fixed), or "not pushed — local merge only"
- Any issues encountered during wrap-up

### Step 5: Next-unit handoff (when iterating through a plan)

This step fires when the user is executing **work units from a multi-unit plan** —
e.g. a tier plan, a roadmap, or a refactor plan with W1, W2, W3... — and there is
a clearly identifiable *next* unit. Skip this step for one-shot evaluations,
discussions, or open-ended improvements where there is no "next unit" to hand off.

**Trigger conditions (all must hold):**
1. The work just completed was scoped to a specific work unit (or contiguous
   units) of a persistent plan in `docs/`.
2. The plan has a next work unit that the user is likely to want done.
3. The user has not already indicated they're stopping for the day or moving
   to unrelated work.

**What to do, automatically — do not wait for the user to ask:**

#### 5a. Assess fresh-session vs continue

Make a deliberate call about whether the next unit should be done in **this**
session or a **fresh** one. State the recommendation and the reasoning to the
user. Be honest — if it's a close call, say so.

**Lean toward continue when:**
- The next unit is in the same module, layer, or pattern as what just shipped
  (e.g. REST routes → MCP tools that mirror them, repository → service that
  calls it).
- The mental model just used is directly load-bearing for the next unit
  (response shapes, recently-decided contracts, fresh dedup logic that the
  next unit reuses).
- Plan-drift patterns or gotchas just discovered are still warm and would
  speed up the next unit's reconnaissance.
- The session is still in its productive window — context is rich but not
  bloated, and the cache is hot.

**Lean toward fresh when:**
- The next unit is in a different module/layer/surface (data plane → CLI,
  backend → frontend, code → docs) — accumulated context becomes dead weight.
- The next unit requires reading a substantially different set of files than
  what's already in context.
- The next unit changes character (e.g. first unit that needs live
  credentials, first unit that touches production data, first unit with a
  user-visible UI to verify) — worth being deliberate with clear scope.
- The session is long enough that reasoning is degrading or the transcript
  is unwieldy.
- The next unit has a different "done" criterion than the cadence just
  established (e.g. "merged + CI green" → "deployed + smoke-tested").

When in doubt, recommend fresh. The cost of a fresh session with a good
prompt is low; the cost of carrying stale context into work it doesn't fit
is silent quality loss.

#### 5b. If recommending fresh — produce a copy-paste prompt

When recommending a fresh session, produce **a single distinct message**
containing the prompt the user can paste directly into the next session.
This is not a summary — it is the *input* to the next `/engineering-team`
invocation. Wrap the body in `[[engteam:prompt-start]]` and
`[[engteam:prompt-end]]` markers (each on its own line at column 0) so
the relay driver can extract it for the next iter and so an interactive
user can copy the body between them cleanly.

The prompt must be self-contained — fresh-session-Claude has no memory of
this conversation, only what the prompt says plus what it can read from
the codebase. Brief it like a smart colleague who just walked into the room.

**Required sections in the prompt** (omit any that genuinely don't apply):

1. **Header** — One sentence naming the work unit and the plan doc that
   defines it (with file path).
2. **State of the world** — Current commit hash on `main`, current test
   count, and a 2-3 sentence summary of what the pipeline / feature / system
   currently does end-to-end. What's wired up, what isn't.
3. **What this unit ships** — One paragraph or short list. The plan has the
   detail; this is the orientation.
4. **Pattern to mirror** — Reference the established cadence (relay
   provisions the worktree → doc → tests → code → full gate (lint + types
   + tests + security) → journal → merge → ask before push → CI watch) so
   the next session doesn't relitigate process decisions.
5. **Recent journal entries to read for context** — Bullet list of the 2-4
   most relevant journal entries (with full paths). These are the onboarding
   pack — they encode the decisions, plan-drift patterns, and gotchas
   accumulated up to this point. Pick the ones that actually inform the
   *next* unit, not the ones nearest in time.
6. **Gotchas to watch for** — Carry forward any cross-unit patterns that
   would otherwise be re-discovered (recurring plan drift, schema
   constraints that keep biting, conventions that aren't in CLAUDE.md).
   When a pattern has happened N times in a row, say so explicitly —
   "this is now the third time" is a stronger signal than "watch for X."
7. **Open questions to think about before coding** — Things the unit's
   plan doesn't fully resolve, where the answer requires reading code that
   the previous session already has loaded but the new one doesn't. Phrase
   as questions, not directives — fresh-session-Claude should make the
   judgment call after reading the relevant code.
8. **Scope notes** — Any explicit out-of-scope items, credential / live-system
   warnings, or "don't bake X into this unit" guidance.

**Tone:** the prompt is operational, not narrative. No lessons-learned
preamble; no "great work last session" framing; no instructions about
how to be a good agent. The new session will read the full skill on
invocation. The prompt's job is to load *this specific unit's* context,
nothing more.

**After producing the prompt message:** end the session naturally. Do not
keep working in the current session unless the user explicitly says to.

**Automation sentinel.** On a line by itself, *immediately after*
`[[engteam:prompt-end]]` (allowing only blank lines between them),
emit:

    [[engteam:handoff]]

This is consumed by the loop driver to detect "more work follows" — see
`../references/sentinels.md` for the full sentinel contract.

> **Self-check before emitting the closing sentinel.** Your iter's last
> non-blank lines MUST match this shape:
>
> ```
> [[engteam:prompt-end]]
>
> [[engteam:handoff]]
> ```
>
> If your draft ends with a triple-backtick fenced block followed by the
> closing sentinel, you have reverted to the pre-2026-05-17 convention
> and the driver will reject the iter. Rewrite using the template below.

Template (copy-paste, fill in the prompt body):

`````
[[engteam:prompt-start]]
<header: one sentence naming the next work unit and its plan doc>

<state of the world: commit hash on main, test count, what's wired
up end-to-end>

<what this unit ships, pattern to mirror, recent journal entries to
read, gotchas, open questions, scope notes — see Step 5b for the
full required-sections list>

For example, the resumed session might need to verify:

```bash
git log -1 --oneline main
uv run pytest -q
```

before starting.
[[engteam:prompt-end]]

[[engteam:handoff]]
`````

#### 5c. If recommending continue — proceed

If you recommended continuing, briefly state the next unit you'd start on
and confirm with the user before launching into it. The user may still
prefer to stop for the day or to switch tasks — recommendation is not
permission.

**Automation sentinel.** Even when recommending continue (intended for an
interactive session), still produce the marker-bracketed next-session
prompt from Step 5b and emit `[[engteam:handoff]]` after it. Headless runners cannot
"continue in the same session" — each iteration of the loop driver is a
fresh harness invocation that needs the same orientation prompt a
fresh user-launched session would. The continue-vs-fresh recommendation
remains useful guidance for the interactive case; the sentinel and prompt
are the contract the loop runs on.

#### 5d. When there is no next unit — emit done

If the trigger conditions for Step 5 do not hold (the plan has no further
work units, the user has stopped for the day, or this run was a one-shot
that did not consume a multi-unit plan), do **not** produce a next-session
prompt. Instead, emit on a line by itself:

```
[[engteam:done]]
```

This tells the loop driver that the plan is exhausted and the automation
should stop.

> **Anti-template.** `done` is the *only* closing sentinel that takes
> no prompt body. Emitting `[[engteam:prompt-start]]` /
> `[[engteam:prompt-end]]` markers before `[[engteam:done]]` aborts
> the run with `[[engteam:done]] cannot have prompt markers ...`. If
> the iter has work to hand off to a next session, the closing sentinel
> is `handoff`, not `done` — return to Step 5b.

#### 5e. When user input is needed mid-run — emit pause-for-input

See `../references/sentinels.md` ("How to pause") for the pause protocol.
The driver treats `pause-for-input` as a valid closing sentinel
for the iter; the worktree is left intact for the resumed session.
Exactly one of `[[engteam:handoff]]`, `[[engteam:done]]`, or
`[[engteam:pause-for-input ...]]` must appear at the end of every Phase 4
run.

> **Self-check before emitting the closing sentinel.** Your iter's last
> non-blank lines MUST match this shape:
>
> ```
> [[engteam:prompt-end]]
>
> [[engteam:pause-for-input id="P<n>" question="..."]]
> ```
>
> If your draft ends with a triple-backtick fenced block followed by the
> closing sentinel, you have reverted to the pre-2026-05-17 convention
> and the driver will reject the iter. Rewrite using the template below.

Template (copy-paste, fill in the prompt body):

`````
[[engteam:prompt-start]]
<resumed-session prompt body — any markdown is fine, including fenced code blocks>

Context for the resumed session: <what you were doing, what decision
is needed, where the worktree / artifacts live>.
[[engteam:prompt-end]]

[[engteam:pause-for-input id="P<n>" question="<one-line summary>"]]
`````

### Simple Wrap-Up (no worktree, no merge needed)

Use this path when no worktree was created (evaluation-only, non-git project, or relay
degraded worktree provisioning because the project is not a git work tree).

1. **If this is a git repo:** Run the Step 1 quality gate normally (tests, lint,
   type-check, security pass, docs, journal, commit). After committing, if the user
   authorized a push, push and watch CI (same Step 3 loop as above). Adapt to the
   project type as described above.
2. **If this is NOT a git repo:**
   - Tell the user where the output files are (evaluation report, plan, etc. — under
     `$RELAY_RUN_DIR`).
   - Ask if they'd like to initialize git and commit the results.
3. **Summary:** Present what was done and where the output files are.
