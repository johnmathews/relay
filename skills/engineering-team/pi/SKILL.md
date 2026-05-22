---
name: engineering-team
description: >
  A senior software engineering team that evaluates, plans, and improves codebases through a
  structured evaluate → plan → develop → wrap-up cycle. The lead engineer works each role's
  lens (product owner, engineer) in a single long-running session per phase. Use this skill
  when the user wants a comprehensive codebase evaluation, an improvement plan, or a full
  evaluate-plan-develop cycle. Trigger when the user mentions: code review, codebase assessment,
  engineering team, project audit, technical debt analysis, code quality review, "evaluate this
  repo", "improve this codebase", "assess this project", architecture review, or any request for
  systematic multi-phase codebase improvement. Also trigger for requests to assess documentation
  accuracy, find bugs systematically, or do a security review of a project. Even if the user
  doesn't say "engineering team", if they're asking for thorough, multi-dimensional project
  analysis, this is the right skill. Also trigger for brainstorming, architecture discussions,
  tradeoff analysis, "should I use X or Y", "help me think about", "discuss", "teach me about",
  or any request where the user wants to explore options and build understanding before
  committing to an approach.
---

# Engineering team — router

This skill orchestrates an evaluate → plan → develop → wrap-up cycle led by
a lead engineer who works each phase as a single long-running session. The
skill is split into a thin router (this file) plus per-phase docs in
`phases/` and cross-cutting reference docs in `references/`.

> **v2 / single-session note.** This is the relay-v2 port of the
> engineering-team skill (spec.md §12, ADR-14). In v1 the lead engineer
> dispatched Task-tool subagents per role. relay-v2 runs
> **one long session per phase** — the "Engineer N" / "Product Owner"
> roles below are *analysis lenses you work yourself in sequence*, not
> agents you spawn. For coarse-grained parallelism across whole
> investigations, relay grew the `fanout` closing sentinel (Phase 9b;
> see `references/fanout.md`): one iter dispatches N parallel child
> runs and the parent resumes with a synthesizer iter once they all
> settle. Use it when two or more genuinely independent investigations
> merge cleanly into a single decision; the in-iter lens work below
> stays sequential.

## Required reading on every iter

Before any phase work, load `references/sentinels.md`. The sentinel
contract governs every emission you make in this iter — `phase-start`,
`unit-start`, `unit-done`, `unit-abandoned`, and the closing sentinels
(`handoff`, `done`, `pause-for-input`) — including the prompt-marker
contract that wraps the next-iter prompt before `handoff` and
`pause-for-input`. This is not on-demand; load it now.

## Decide which phase to load

When this skill activates, your first action is to determine the current
phase and load the matching `phases/phase-N-<name>.md`.

**If a `RELAY_PHASE:` line is present in the preamble** (relay-driven runs):
load the file matching that phase name. The mapping is:

- `evaluation` → `phases/phase-1-evaluation.md`
- `planning` → `phases/phase-2-planning.md`
- `development` → `phases/phase-3-development.md`
- `wrap-up` → `phases/phase-4-wrap-up.md`

**`RELAY_RUN_DIR` (always present in relay-driven runs).** All artifacts
for this run — `evaluation-report.md`, `improvement-plan.md`,
`discussions/`, anything else — MUST be written under `$RELAY_RUN_DIR/`.
relay resolves `RELAY_RUN_DIR` to `<project_root>/.relay/runs/<run_id>/`
(spec.md §3.3): the canonical artifacts directory, a **sibling of the
per-run worktree, never nested inside it**. Always write to
`$RELAY_RUN_DIR/<artifact>` — never to a path inside the worktree. If
`RELAY_RUN_DIR` is absent (interactive use outside relay), create
`.relay/runs/manual-<utc>/` on first write and use that as your run dir.

**If no `RELAY_PHASE:` is set** (interactive or first-iter use): infer the
phase from on-disk state:

1. If `$RELAY_RUN_DIR/evaluation-report.md` does not exist → load `phases/phase-1-evaluation.md`.
2. Else if `$RELAY_RUN_DIR/improvement-plan.md` does not exist → load `phases/phase-2-planning.md`.
3. Else if the plan has at least one work unit with no matching `unit-done` → load `phases/phase-3-development.md`.
4. Else → load `phases/phase-4-wrap-up.md`.

If the user has explicitly asked for the Discussion workflow instead of
Build (e.g. "let's discuss the architecture before I commit to a plan"),
load `references/discussion.md` instead and follow it. The Discussion
workflow does not use phase-start sentinels.

## Emit phase-start immediately

Once you've loaded the matching phase doc, your next action is to emit on
its own line at column 0:

    [[engteam:phase-start phase="<name>"]]

with `<name>` being one of `evaluation`, `planning`, `development`,
`wrap-up`. See `references/sentinels.md` for the full sentinel contract.

If a single iter completes one phase and naturally begins another (e.g.
Phase 1 → Phase 2 after synthesis), emit `phase-start` once per phase
entered, in order.

## Sentinel contract

The complete contract is in `references/sentinels.md`. Read it before
emitting any sentinel. The driver (`relay`) parses these markers from
assistant text and uses them to decide whether to continue, stop, or pause
the loop. Skipping or misformatting sentinels causes the run to abort.

The eight verbs at a glance: `phase-start`, `unit-start`, `unit-done`,
`unit-abandoned`, `handoff`, `done`, `pause-for-input`, `fanout`.

## Cross-cutting references

Load these on demand when their topic becomes relevant:

- `references/team-structure.md` — roles as single-session lenses, output
  formatting, and how to ask questions.
- `references/workflows.md` — Build vs Discussion overview.
- `references/worktree.md` — working-directory invariants: relay
  provisions the per-run worktree, the skill does not.
- `references/discussion.md` — Discussion workflow details.
- `references/fanout.md` — the `fanout` closing sentinel: when to dispatch
  parallel child runs and how the synthesizer iter resumes.
- `references/general-guidelines.md` — cross-cutting rules and the triage
  entry point for urgent reports.

## What this router does NOT contain

This file is intentionally short. It does NOT contain:

- Per-phase steps — those are in `phases/`.
- The sentinel contract details — that's in `references/sentinels.md`.
- Team / workflow / worktree details — those are in `references/*.md`.

When in doubt, the per-phase doc is authoritative for that phase's behavior.
