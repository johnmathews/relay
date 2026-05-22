# Phase 2: Planning

> Loaded when the preamble specifies `RELAY_PHASE: planning`, or when an
> evaluation report exists at `$RELAY_RUN_DIR/evaluation-report.md` and no
> improvement plan has been written yet.

## Emit phase-start immediately

Before any other action in this phase, emit at column 0:

    [[engteam:phase-start phase="planning"]]

## Frontmatter on the improvement plan (required)

The plan file (`$RELAY_RUN_DIR/improvement-plan.md`) MUST begin with a
YAML frontmatter block delimited by `---` lines, listing every work unit
with its `id` and `title`. This is the machine-readable contract that
automation tools (loop drivers, dashboards) parse to track progress. The
IDs declared here are authoritative: every Phase 3 sentinel must reference
them verbatim. Format:

    ---
    plan: <short kebab-case name>
    units:
      - id: W1
        title: <title that matches the work-unit heading below>
      - id: W2
        title: <...>
    ---

Use IDs of the form `W<n>` numbered from 1 in the order units are listed.
Do not reuse or renumber IDs after the plan is approved — Phase 3 sentinels
and dashboard state are keyed on them. If a unit is added later, give it
the next free `W<n>`.

Also add an **ID:** line to each per-work-unit field list so each unit
references its frontmatter ID explicitly.

## File persistence mandate

The improvement plan MUST be written to `$RELAY_RUN_DIR/improvement-plan.md`
on disk using the Write tool — not produced inline in the chat.
`$RELAY_RUN_DIR` is `<project_root>/.relay/runs/<run_id>/` (spec.md §3.3),
a sibling of the worktree. If `$RELAY_RUN_DIR` does not exist, `mkdir -p`
it first. Do not proceed past Phase 2 to Phase 3 until the file exists on
disk. (Interactive use outside relay: `open
$RELAY_RUN_DIR/improvement-plan.md` after writing.)

---

The goal is to produce a concrete, actionable improvement plan based on the evaluation findings.

### Step 1: Prioritize

Review the evaluation report and categorize findings by:
- **Critical:** Bugs, security issues, data loss risks — fix first
- **High:** Significant quality issues, missing tests for core paths
- **Medium:** Code quality improvements, documentation gaps
- **Low:** Nice-to-haves, minor style issues

**Priority captures urgency, not blast radius.** A trivial typo fix and a multi-day auth
refactor can both be "High" priority but carry very different risk if the change goes
wrong. Risk is a separate axis on each work unit (see Step 3) — don't conflate the two.

### Step 2: Plan Development

Do not assume what changes are needed — base every recommendation on specific findings from
the evaluation report. If the evaluation didn't identify a problem, don't invent one. If you're
unsure whether something is an issue, go back and verify before planning a fix for it.

**Read the code before specifying any change.** Plans drafted from the evaluation summary alone
routinely turn out structurally wrong — the function has been renamed, the abstraction lives in
a different module, the issue was already fixed in passing. The cost is real: a code-ungrounded
plan typically gets rewritten as a "v2" once implementation starts, with the rewrite consuming
more time than the original code-reading would have. One pass of code reading at planning time
prevents that cycle. If you are proposing changes to a file, you must have read that file.

Work each planning lens yourself, in sequence (relay-v2 MVP is
single-session — see `../references/team-structure.md`):

**Code-improvements lens:**
- For each code issue, specify: what file, what change, why, and which specific existing
  tests will need updates, deletion, or new siblings (read the test file — don't guess).
  This populates the Test impact field on the work unit.
- When proposing changes that involve third-party APIs, SDKs, or libraries, reference the
  web research from Phase 1 (the Engineer 1 lens findings) — see `phase-1-evaluation.md`. If the
  specific API or pattern wasn't covered in Phase 1, use `WebSearch` and `WebFetch` to look
  it up now — don't guess from training data.
- Group related changes into logical units of work
- Identify dependencies between changes (what must happen first)

**Test-improvements lens:**
- For each test gap, specify: what to test, what kind of test, where it goes
- For existing tests that need changes, specify what changes and why
- Include edge cases identified during evaluation

**Documentation-improvements (Product Owner) lens:**
- For each doc gap, specify: what to document, where it goes, what to reference
- For inaccurate docs, specify what's wrong and what the correct information is
- Plan any new documentation files needed

### Step 3: Plan Review

As Lead Engineer, review the per-lens plans and synthesize into a single improvement plan.

**File persistence is mandatory, not optional.** You MUST write the plan to
`$RELAY_RUN_DIR/improvement-plan.md` on disk using the Write tool — not
produce it inline in the chat, not embed it in a commit message, not describe
it in prose. The file on disk is the contract Phase 3 reads from and that
external automation (loop driver, dashboard) parses for YAML frontmatter and
work-unit IDs. If `$RELAY_RUN_DIR` does not exist, `mkdir -p` it first.
**Do not proceed to Phase 3 until the file exists on disk** — a Phase 3 run
without a written plan file is a contract violation, even if the plan exists
in chat.

The plan must contain (in order):

**Frontmatter (required)** — The file must begin with a YAML frontmatter block delimited
by `---` lines, listing every work unit with its `id` and `title`. This is the machine-
readable contract that automation tools (loop drivers, dashboards) parse to track progress.
The IDs declared here are authoritative: every Phase 3 sentinel must reference them
verbatim. Format:

```
---
plan: <short kebab-case name>
units:
  - id: W1
    title: <title that matches the work-unit heading below>
  - id: W2
    title: <...>
---
```

Use IDs of the form `W<n>` numbered from 1 in the order units are listed. Do not reuse
or renumber IDs after the plan is approved — Phase 3 sentinels and dashboard state
are keyed on them. If a unit is added later, give it the next free `W<n>`.

**Non-goals** — A short list of things this plan is *not* doing. Examples: "not optimizing
cold-start latency in this round," "not refactoring the auth module — separate plan,"
"not changing the public API surface." Non-goals prevent scope creep more reliably than
per-finding out-of-scope notes, because they are stated up front rather than buried per-issue.
A plan with no non-goals is suspicious — almost every real plan is shaped as much by what
it deliberately excludes as by what it includes.

> **Coarse-grained parallelism via fanout.** When the plan identifies 2+ genuinely
> independent units whose results merge cleanly into a single follow-on decision,
> consider emitting `[[engteam:fanout]]` from the closing step of Phase 2 instead of
> the usual `pause-for-input` handoff to Phase 3, and letting each unit run as its
> own child run. See [`../references/fanout.md`](../references/fanout.md) for the
> decision criteria and the grammar. This is appropriate only when the parallel
> exploration is worth the fixed cost of a fresh pi process per child; for routine
> sequential work the standard Phase-3 handoff is the right call.

**Work units** — A sequence of work units. Each unit contains:

- **ID:** The `W<n>` identifier from the frontmatter. Must match exactly — this is the
  key that Phase 3 sentinels and dashboards use to track the unit.
- **Title:** What this unit accomplishes. Must match the title in the frontmatter.
- **Priority:** Critical / High / Medium / Low — urgency, when this should be done.
- **Risk:** Low / Medium / High — blast radius if the change goes wrong. High = touches
  auth, migrations, persistent state, hot paths, public API, or anything where a bad
  deploy is hard to reverse. Risk is independent of priority — a Low-priority unit can
  be High-risk (e.g. a "nice-to-have" refactor that touches the auth module).
- **Size:** S / M / L — rough effort. S = under an hour. M = a single session. L =
  multi-session. Flag any L unit for splitting unless splitting would create artificial
  seams (e.g. a coherent migration that can't be half-shipped). If a unit is "too big to
  estimate confidently," that itself is a finding — say so and propose a spike instead
  of a plan.
- **Changes:** Specific files and modifications (code, tests, docs).
- **Test impact:** Which existing tests will need updates, deletion, or new siblings.
  "None" is a valid answer but must be deliberate — read the test file before claiming it.
  Discovering broken tests in Phase 3 that the plan didn't predict is a planning failure.
- **Reversibility:** How to back this out if it ships and goes wrong. "Pure code change,
  revert commit" is fine for most units. For migrations, data backfills, schema changes,
  or config changes affecting prod: name the explicit rollback path (down-migration,
  reverse backfill, kill switch). If a change is genuinely irreversible (data deletion,
  one-way migration), say so explicitly — that signals "review extra carefully" to
  implementation and to the user.
- **Dependencies:** Hard dependencies — units that must complete first. Note soft
  dependencies separately ("easier after W3 but not blocked by it").
- **Acceptance criteria:** Specific, observable conditions. "Tests pass" is not enough —
  name *which behavior* is verified by *which test*, or what the user-visible outcome is.
  For doc changes, name the section that exists and is correct. The criterion should let
  someone other than the implementer judge whether the unit is done.

**Ordering** — When multiple units have no hard dependency between them, default to:
foundation-first (units that other units build on), then risk-first (high-risk units go
early so problems surface while context is fresh and the plan can still be revised),
then quick wins. Do not default to "the order you wrote them in" — that is not
ordering. State the chosen ordering rationale in one sentence at the top of the
work-unit list.

Ensure the plan is complete — every issue from the evaluation should be addressed or
explicitly marked as out-of-scope with a reason. (Non-goals capture *categories* of
out-of-scope work; per-finding notes capture specific exclusions within scope.)

### Step 4: User confirmation before Phase 3

Phase 2 ends with an explicit user gate. Do not proceed to Phase 3 (development) until
the user has confirmed the plan or applied edits. The user opening the file in a viewer
is not enough — Phase 3 is the expensive phase, and a 60-second confirmation prevents
hours of rework on plan content the user would have changed.

This gate is enforced by emitting `pause-for-input` — a *closing sentinel*. Like
`handoff`, it requires a `prompt-start` / `prompt-end` marker pair wrapping the
next-session prompt that the resumed run will receive. Missing markers → driver
exits 1 and the user cannot resume cleanly.
Read `references/sentinels.md` if you have not already.

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
Plan saved at `$RELAY_RUN_DIR/improvement-plan.md`. Three work units:
W1 ..., W2 ..., W3 .... Reply "go" to start Phase 3, or paste edits / call
out units to drop, reshape, or reorder.

[[engteam:prompt-start]]
Phase 2 is complete. The improvement plan is at
`$RELAY_RUN_DIR/improvement-plan.md`. Re-read it in full — the user may
have edited it. Then begin Phase 3 (development) by emitting
`phase-start phase="development"` and following
`~/.claude/skills/engineering-team/phases/phase-3-development.md`.
[[engteam:prompt-end]]

[[engteam:pause-for-input id="P1" question="Approve plan in $RELAY_RUN_DIR/improvement-plan.md and proceed to Phase 3 (development)?" review_path="improvement-plan.md"]]
`````

Notes:

- The conversational nudge to the user comes first, in plain prose.
- The `prompt-start` / `prompt-end` body is the prompt for the *resumed*
  session, not for the user — write it as instructions to your future self.
- Increment the `P<n>` id if this run has already paused before; for the
  first pause in a run, `P1` is correct.
- The `~/.claude/skills/engineering-team/...` path is the default
  `relay install-skill` target. If the skill was installed with
  `--project`, substitute that project's `.claude/skills/...` path.
- The `review_path="improvement-plan.md"` attribute (sentinel grammar
  from sub-phase 14b) tells relay's dashboard which file the operator
  should review inline. The dashboard renders a textarea + markdown
  preview alongside the answer textarea; each Save lands an
  `artifact_edited` event in the run's event store (ADR-40) before
  the operator clicks Resume. The attribute is **relative to
  `$RELAY_RUN_DIR`** — `..`, absolute paths, empty strings, and NUL
  bytes are rejected at parse time. All sentinel attributes must
  appear on the same line as the verb (line-anchored parser); see
  `../references/sentinels.md` § "Reviewable pauses" for the full
  grammar. Omitting the attribute keeps the pre-14b behaviour (no
  inline editor; the operator opens the file in their own tool).

If the user edits the plan, re-read it in full before starting Phase 3 — don't assume
the edits were cosmetic. If the user changes priorities, ordering, or scope, the
implementation plan changes accordingly.

This gate applies whenever Phase 3 is going to run. If the user invoked only "evaluate"
or "plan" (Phase 1 or Phases 1-2), there is no gate to enforce — the plan is itself the
deliverable.

### Step 5: Handoff template (no-pause path)

If the user pre-approved the plan or you're running an unattended cycle
where Phase 2 → Phase 3 is non-interactive, the closing sentinel is
`handoff`, not `pause-for-input`. The marker contract is the same shape.

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
Phase 2 is complete. The improvement plan is at
`$RELAY_RUN_DIR/improvement-plan.md`. Begin Phase 3 (development) by
emitting `phase-start phase="development"` and following
`~/.claude/skills/engineering-team/phases/phase-3-development.md`.

The user pre-approved this plan — proceed without further confirmation.
[[engteam:prompt-end]]

[[engteam:handoff]]
`````

### Plan hygiene and persistence

The improvement plan in `$RELAY_RUN_DIR/improvement-plan.md` is a working document — Phase 3
consumes it and it isn't meant to outlive the session. But the user sometimes asks for a plan
that *will* outlive this session: a refactor roadmap, a multi-week initiative plan, a feature
plan referenced from `docs/`. When the plan is **persistent** (will live in `docs/` and be
referenced in future sessions), apply these conventions — they prevent the failure modes that
make older planning docs hard to maintain (shadow inventory, no clear status, plan content
buried under execution sequencing).

1. **Status header at the top.** Every persistent plan begins with:
   ```
   **Status:** active. **Last updated:** YYYY-MM-DD. **Supersedes:** <doc-or-none>.
   ```
   This lets a future reader tell at a glance whether the doc is live, stale, or superseded —
   without cross-referencing a roadmap.

2. **Index it from the canonical roadmap immediately.** If the project has a `docs/roadmap.md`
   (or equivalent — check during Phase 1), add a link to the new plan from the roadmap in the
   same edit that creates the plan. A plan that isn't indexed becomes shadow inventory —
   discoverable only by a reader who already knows it exists.

3. **Decisions first, execution second.** Lead with a short "Decisions & tradeoffs" section
   *before* the work-unit list. For each cross-cutting decision: what was chosen, what was
   rejected, why. The decisions are the part of the plan with long-term value; work units age
   out as soon as they're executed. Putting decisions at the top means they survive even when
   the execution sequence shifts.

4. **Prefer shorter docs, no hard cap.** Length is fine if the scope warrants it.
   The real failure mode is *content density* — padding, restated background,
   execution sequencing buried under decisions. If a plan is hard to re-read,
   the right responses are: split into decisions + execution docs, cut
   restate-from-elsewhere content, or use headings / TOC so readers can navigate
   without reading top-to-bottom. Judge by re-readability, not character count.

5. **Kill criteria** (multi-week initiatives only). For a plan that spans multiple sessions or
   weeks, include a "Kill criteria" section: what would invalidate this plan? ("We'd abandon
   this if library X gets deprecated"; "We'd redesign if assumption Y turns out wrong.") This
   forces clarity about what would change our minds. Skip for tactical work — a bug-fix plan
   doesn't need kill criteria.

6. **Superseding or closing a plan — archive it.** When a new plan replaces an existing one, or
   a plan is closed (all work units shipped), add a `**Status:** superseded by
   [new-plan.md] (YYYY-MM-DD).` or `**Status:** closed YYYY-MM-DD.` header to the top of the old
   plan, then `git mv` it into `docs/archive/` and update inbound links from any active docs.
   This keeps the rationale and decisions accessible while keeping the active `docs/` listing
   easy to scan. Do not just leave closed/superseded plans alongside active ones — the listing
   becomes shadow inventory and readers waste time triaging which docs to trust.

For a one-shot improvement plan that lives only in `$RELAY_RUN_DIR/` and is consumed by
Phase 3, conventions 1, 2, 5, and 6 don't apply — just write the work units. The hygiene rules
are about *persistence*, not ceremony for its own sake.
