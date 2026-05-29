# Phase 3: Development

> Loaded when the preamble specifies `RELAY_PHASE: development`, or when an
> improvement plan exists with at least one open work unit.

## Emit phase-start immediately

Before any other action in this phase, emit at column 0:

    [[engteam:phase-start phase="development"]]

## You are already in a relay-provisioned worktree — do NOT create one

This is the load-bearing v2 difference (ADR-13, ADR-14). In v1 the
Phase-3 skill ran `git worktree add` itself. **In relay the
orchestrator provisioned the per-run worktree before your session
started** and set your working directory inside it. Your job is to
*verify* that, not to create a worktree.

```bash
git rev-parse --show-toplevel   # expect a path ending /.relay/worktrees/<run_id>
git branch --show-current       # expect relay/<run_id>
```

- If those match: good — all code, test, and doc changes happen here.
  `main` stays untouched until Phase 4 merges the per-run branch.
- If `git rev-parse --show-toplevel` is the **project root** (no
  worktree): relay degraded provisioning because the project is not a
  git work tree (ad-hoc dir, fixture). Work directly in the project
  root; Phase 4's merge step degrades to a plain commit. Do **not**
  create a worktree to "fix" this — relay owns workspace provisioning,
  and a skill-created nested worktree breaks the dashboard's run
  resolver and the Phase-4 merge.

There is no `current.txt` mirror step in v2 — run-dir resolution is
driver-side (ADR-13). Phase artifacts still go to `$RELAY_RUN_DIR`
(`<project_root>/.relay/runs/<run_id>/`), a sibling of the worktree.
The full discipline is in `../references/worktree.md` — load it before
your first edit if anything above is unclear.

## Sentinel contract (read first)

Phase 3 emits the full sentinel set: `unit-start`, `unit-done`,
`unit-abandoned`, plus exactly one closing sentinel per iter (`handoff`,
`done`, or `pause-for-input`). The complete contract lives at
`../references/sentinels.md`. Read that file before starting any work unit.

The rules in summary:

- Emit `unit-start id="W<n>" title="..."` immediately BEFORE the first
  edit for that unit.
- Emit `unit-done id="W<n>" title="..."` AFTER your lead-engineer review
  step passes and the full test suite is green.
- Emit `unit-abandoned id="W<n>" reason="..."` instead of `unit-done` if
  the unit cannot complete in this session.
- Every `unit-start` must be paired with exactly one of `unit-done` or
  `unit-abandoned` before the iter ends.

## Pausing for user input

If during Phase 3 you encounter a decision that genuinely needs user
input, follow the pause protocol described in `../references/sentinels.md`
("How to pause"). Do not pick on the user's behalf and do not silently
continue.

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

Context for the resumed session: <what you were doing, what file you
were editing, what decision is needed>.
[[engteam:prompt-end]]

[[engteam:pause-for-input id="P<n>" question="<one-line summary>"]]
`````

`<n>` is the next free pause id (P1 for the first pause in this run,
P2 for the second, etc.). The question is a one-line summary; multi-line
context goes inside the marker body.

## Closing decision when the LAST unit completes

When you emit `unit-done` for the final unit in the plan, do **not** emit
`[[engteam:done]]` directly. Plan exhaustion is your cue to enter Phase 4
wrap-up, not your cue to terminate the run. The cycle hasn't ended yet —
the worktree still needs to be merged, the cumulative commit still needs
to land on `main`, and the run needs a summary.

Choose one:

1. **Cross into Phase 4 in this same iter** (preferred when wrap-up is
   light):
   - Emit `[[engteam:phase-start phase="wrap-up"]]` on its own line.
   - Load `phase-4-wrap-up.md` and follow its steps. **Step 1 is the
     full quality gate (lint + types + full test suite + security
     review + journal) — that step is mandatory regardless of how
     light wrap-up looks.** Merging without it is a contract violation,
     even if the work is committed and tests passed unit-by-unit: the
     unit loop does not run lint, type-check, or a security pass.
   - End the iter with `[[engteam:done]]` once wrap-up is complete.

2. **Hand off to a dedicated Phase 4 iter** (preferred when wrap-up has
   real work — large merge, CI to watch, multi-step cleanup):
   - Emit `[[engteam:handoff]]` with a marker-bracketed next-session
     prompt (`[[engteam:prompt-start]]` ... `[[engteam:prompt-end]]`)
     that begins "Phase 4 wrap-up — emit `phase-start phase=\"wrap-up\"`
     and follow `phase-4-wrap-up.md`."
   - The next iter does the wrap-up and emits `done`.

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
Phase 4 wrap-up — emit `phase-start phase="wrap-up"` and follow
`phase-4-wrap-up.md`.

Context for the resumed session:

- Worktree: `<path on disk>` on branch `<branch>`.
- Plan: all units complete, full test suite green (run `<command>` to
  confirm).
- Phase 4 Step 1 is mandatory: run the full gate (lint + types + tests
  + security review + journal).

Then merge per the wrap-up phase doc.
[[engteam:prompt-end]]

[[engteam:handoff]]
`````

Emitting `[[engteam:done]]` directly after the final `unit-done` is a
bug: it tells relay the run is over while skipping the merge, the
commit, and the summary the cycle is supposed to end with. The driver
will exit 0 and you'll be left with uncommitted work on the per-run
branch.

---

The goal is to implement the improvement plan using a test-driven development approach.
Only begin this phase when the plan from Phase 2 is complete and coherent.

### Approach: Documentation First, Then Tests, Then Code

For each work unit in the plan, follow the order appropriate to the work unit's content:

**Code work units** (the work unit modifies or creates executable code):
1. **Documentation:** Write or update the relevant documentation first. This forces
   clarity about what the change should accomplish before writing any code.
2. **Tests:** Write the tests that will verify the change. These tests should fail
   initially (red phase of TDD). For existing test modifications, update tests to
   reflect the new expected behavior.
3. **Code:** Implement the change to make the tests pass. Keep changes minimal —
   make the tests green, nothing more.
4. **Verify:** Run the **full** test suite. All tests must pass before moving to the next unit.

**Bug fix work units** (the work unit fixes a bug found during evaluation or triage):
1. **Reproduce:** Write a failing test that reproduces the exact bug. The test must
   fail before the fix and pass after. This proves the bug exists and prevents regressions.
2. **Fix:** Implement the minimal code change to make the test pass.
3. **Verify:** Run the **full** test suite to confirm no regressions.

**State-transforming work units** (DB migrations, data backfills, file-format
upgrades — anything that reshapes existing persistent data):

The failure mode here is **data-shape, not code-shape** — standard tests verify
the resulting schema, but the real bugs live in data shapes synthetic fixtures
don't reproduce. Apply two extra disciplines on top of normal tests:

1. **Probe the real data first.** Before designing the transform, query the
   actual store (or a snapshot) for anomalies the transform assumes away —
   orphan FK references, NULLs in "populated" columns, duplicates that would
   violate a planned UNIQUE, CHECK violations, encoding oddities, mixed-type
   columns. Real stores accumulate these; synthetic data omits them. If the
   store is untouchable, ask the user to run the probe queries.
2. **Re-runnable from any partial state.** If the runtime doesn't roll back
   mid-transform on failure (SQLite `executescript`, shell scripts, ad-hoc
   fixers), an aborted run leaves leftover state that breaks the next attempt.
   Drop intermediates at the top, use idempotent operations
   (`DROP IF EXISTS`, `INSERT OR IGNORE`), assume any step might fail.

Tests must include at least one "dirty" prod-shaped fixture (orphans, dupes,
partial-failure leftovers) and assert the transform completes. Schema-only
tests on a fresh DB cover the destination, not the journey — say so explicitly
when reporting status.

**Non-code work units** (documentation fixes, config changes, YAML/markdown updates,
CI/CD workflow creation, .gitignore updates, etc.):
1. Make the changes directly.
2. If the change affects something testable (e.g., a CI workflow), validate it where
   possible (e.g., lint the YAML, dry-run the workflow).
3. No TDD cycle needed — do not write tests for markdown or configuration.

### Test Runner Detection

Automatically detect the project's test framework(s):
- Look for `pytest.ini`, `setup.cfg [tool:pytest]`, `pyproject.toml [tool.pytest]` → pytest
- Look for `package.json` with test script, `jest.config.*`, `vitest.config.*` → npm test / jest / vitest
- Look for `go.mod` → go test
- Look for `Cargo.toml` → cargo test
- Look for `Makefile` with test target → make test
- Multiple test runners may coexist (e.g., Python backend + JS frontend)

Run the full test suite after each work unit completes. If tests fail, fix before proceeding.

When running the final test suite after all work units are complete, generate an HTML coverage
report. This is the only time an HTML report is generated (Phase 1 only records the percentage):
- **Python (pytest):** `coverage run -m pytest && coverage html` → `htmlcov/`
- **JS/TS:** `npx c8 --reporter=html` or `nyc --reporter=html`
- **Go:** `go test -coverprofile=coverage.out ./... && go tool cover -html=coverage.out -o coverage.html`
- **Rust:** `cargo tarpaulin --out html`

Include the final coverage percentage in the report alongside the Phase 1 baseline for comparison.

### Implementation

Implement the work units yourself, in dependency order, within this one
session (the in-iter model is single-session — see
`../references/team-structure.md`). Units with hard dependencies must run
in order; independent units are still done sequentially here, just
without inter-unit dependencies forcing the order. Do NOT create nested
worktrees for "parallel" units — the single relay-provisioned worktree
is the only workspace.

> **Coarse-grained parallelism via fanout.** When the plan lists 2+ work
> units with no inter-dependencies AND each unit's changes are large
> enough that wall-clock matters, you may close this iter with
> `[[engteam:fanout]]` and let each unit run as its own child run on a
> child branch. Each child does its own TDD cycle in its own worktree;
> the synthesizer iter merges the results. Read
> `../references/fanout.md` for the grammar; this is appropriate when
> the merge is genuinely clean (no overlapping diffs on the same files),
> not as a default.

For each work unit:
- Re-read the specific work unit from the improvement plan
- Follow the doc → test → code order strictly
- Do not assume how existing code works — read it before modifying it
- Do not assume tests will pass — run them and check the actual output
- Run the full test suite after implementation

As Lead Engineer, review each completed unit before marking it done. Check:
- Does the implementation match the plan?
- Are the tests meaningful (testing behavior, not implementation)?
- Is the documentation accurate and clear?
- Were any unnecessary changes introduced?

**Sentinel cadence (recap — see `../references/sentinels.md` for the full rules).**
For every work unit you touch in this phase: emit `[[engteam:unit-start id="W<n>" title="..."]]`
before the first edit, then `[[engteam:unit-done id="W<n>"
title="..."]]` after your review confirms the unit is green, or `[[engteam:unit-abandoned
id="W<n>" reason="..."]]` if you give up on it. Never leave a `unit-start` open at the end
of a session.

### Journal Entry

**Do not write the journal entry here.** Phase 4 wrap-up Step 1 writes the
journal as part of the quality gate. Writing it here would create a
duplicate. Instead, ensure the work done during Phase 3 is captured in the
conversation context so the Phase 4 journal step can include it.

(relay has no `/done` slash-skill — pi is the harness, not Claude
Code. Phase 4 performs the gate's steps inline; see
`phase-4-wrap-up.md`.)

If wrap-up will use the Simple Wrap-Up path (no worktree — non-git
project), and only then, write the journal entry here:

Filename format: `YYMMDD-descriptive-name.md` (e.g., `250321-security-fixes-and-test-coverage.md`)

The journal entry should document:
- What was changed and why
- Key decisions made during implementation
- Issues discovered during development that weren't in the original plan
- Test coverage changes
- Infrastructure and tooling changes
- Any remaining concerns or follow-up items
