# Proposal — harness-specific skill variants

**Status:** proposal (not yet ADR'd, not yet implemented)
**Date:** 2026-05-21
**Touches:** `skills/`, `src/relay_v2/cli/install_skill.py`, `docs/skills.md`, `tests/cli/`, `tests/skills/`
**Does not touch:** orchestrator, harness, REST/MCP, event store, sentinel grammar

## Background

The bundled `engineering-team` skill (`skills/engineering-team/`, 11 docs)
is a **port** of the v1 Claude Code skill, executed in Phase 6
(ADR-28). Six deliberate adaptations made the port pi-shaped:

1. Single-session execution (no Task-tool subagent dispatch).
2. Role names become single-session *analysis lenses*.
3. Paths re-rooted to `.relay/runs/<run_id>/` (artifacts) and the
   relay-provisioned worktree (skill **verifies**, never creates).
4. Wrap-up gate inlined — `/done` and `/merge-push` (Claude Code
   slash commands) replaced with explicit shell steps.
5. Sentinel-grammar references repointed at `references/sentinels.md`.
6. Example commands rewritten as `uv run …`.

Adaptations 1, 4, and 6 in particular are *behavioural* — they change
what the skill tells the agent to do, not just paths or names.

## State of the world (today)

- One harness in production: **pi** (`harness/pi.py`, ADR-04).
- One bundled skill: `skills/engineering-team/`.
- The bundled skill **is** the pi variant — but it isn't labelled as
  such. The Claude Code original lives outside the repo (in v1) and is
  no longer the source of truth for anything relay ships.
- `relay install-skill` resolves `skills/engineering-team/` and copies
  it verbatim to `~/.claude/skills/engineering-team/` (or
  `<project>/.claude/skills/engineering-team/` with `--project`).
  Resolver: `src/relay_v2/cli/install_skill.py:skill_source_dir` —
  tries the wheel-packaged path (`relay_v2/skills/<name>`), falls back
  to the repo-root layout. ~100 LoC, single skill name constant.
- ADR-04 already commits to the multi-harness future
  ("harness isolation; orchestrator sees normalized `HarnessEvent`
  types only"), but no scaffolding exists for harness-aware skills.

## Why this matters now (and what triggers it)

Two pressures converge:

- **The current "single skill = pi skill" is implicit.** If/when a
  second harness arrives (Claude Agent SDK, or a fresh-fork pi), there
  is no clean place to put a variant. Today's layout commits, by
  accident, to one skill = one shape.
- **Adaptations 1 + 4 are not "tweaks", they're a different program.**
  Pi's variant says "after wrap-up, run these shell commands";
  Claude Code's says "invoke `/done` then `/merge-push`". You cannot
  template-merge these without losing the precise wording each agent
  expects. The variants want to be *parallel sources*, not
  diff-able derivatives of one canonical doc.

Neither pressure is acute today. The proposal is a **structural
cleanup** that prevents future divergence-by-accident, not a feature.

## Proposal

### Directory layout

```
skills/
  engineering-team/
    README.md                # the shared "what is this skill, why does it exist"
    pi/                      # pi-shaped variant (today's contents move here)
      SKILL.md
      phases/
        phase-1-evaluation.md
        phase-2-planning.md
        phase-3-development.md
        phase-4-wrap-up.md
      references/
        sentinels.md
        team-structure.md
        workflows.md
        worktree.md
        discussion.md
        general-guidelines.md
    # claude-code/ — added when/if a second variant is ported.
    #                Do NOT scaffold an empty directory now.
```

### `relay install-skill` change

A new optional `--harness` flag, defaulting to `pi`:

```
relay install-skill                            # → pi variant (default)
relay install-skill --harness pi               # explicit
relay install-skill --harness claude-code      # errors today: variant not present
relay install-skill --project PATH --harness pi
```

Resolver becomes
`skill_source_dir(name="engineering-team", harness="pi")` and returns
`<bundled>/<name>/<harness>/`. The wheel `force-include` (`pyproject
.toml`) already maps the whole `skills/` tree, so packaging needs no
change beyond the new subdirectory existing.

Target path stays the same:
`~/.claude/skills/engineering-team/` (no harness suffix at the
destination — Claude Code et al. read `engineering-team`, not
`engineering-team-pi`). Rationale: the *agent* reads the skill; the
harness suffix exists only at the *bundle* layer for variant
selection. Mixing harnesses in one Claude install would be a
configuration error worth a separate flag, not the default shape.

### What the README.md at the skill root says

One short page: skill purpose, the four-phase workflow shape, the
sentinel-driven handoff model, a pointer to each variant subdirectory
("`pi/` is the relay+pi shape; see `pi/SKILL.md`"). Roughly 40 lines.
It is **not** a SKILL.md — agents never load it; humans (and the
install-skill resolver) do.

### What stays unchanged

- Sentinel grammar (lives inside `pi/references/sentinels.md`,
  verbatim from spec §12; unchanged).
- Phase structure (4 phases; unchanged).
- The six Phase 6 / ADR-28 adaptations (now self-evidently scoped to
  the `pi/` variant; that's the point).
- Tests in `tests/skills/test_skill_structure.py` — they lock the
  deliverable shape; they need path updates but assert the same things.
- `tests/cli/test_install_skill.py` — needs a `--harness pi` test and
  a `--harness <unknown>` failure test; existing tests adjust paths.

## Tradeoffs and rejected alternatives

### Considered: shared core + harness adapters (templated)

Single `engineering-team/SKILL.md` with `{{#if harness=pi}}` /
`{{#if harness=claude-code}}` blocks, rendered at install time.

**Rejected because**: the differences aren't presentation, they're
*workflow shape* (subagent dispatch vs sequential lens-switching is
not one workflow with two formatting variants). Templating would
either (a) hide the differences behind conditionals — readers can't
see one variant cleanly — or (b) be so coarse-grained that the
template engine is doing nothing the directory tree wouldn't do
better. With two harnesses and one skill, two parallel docs are
strictly easier to keep correct than one templated doc. Reconsider
if the catalogue grows to ≥3 harnesses **and** ≥5 skills.

### Considered: flat `engineering-team-pi/` (no nesting)

`skills/engineering-team-pi/` and `skills/engineering-team-claude-code/`
as peer top-level directories.

**Rejected because**: it loses the "these are variants of one skill"
grouping. The shared README has nowhere to live, and `relay
install-skill engineering-team` has to know about the harness suffix
convention rather than treating it as a normal sub-selection. Nesting
makes the variant relationship structural.

### Considered: keep today's layout; add `claude-code/` only when needed

Defer the rename until a second variant actually exists.

**Tradeoff**: cheaper now (zero work), but at second-variant time you
do *both* the rename **and** the new variant in one PR, which mixes
concerns. The structural change is small and self-contained; doing it
once when the answer is obvious beats doing it later when there's also
a new skill to review.

### Considered: variant selection by env var / config, not CLI flag

E.g., `RELAY_HARNESS=pi relay install-skill`.

**Rejected because**: install-skill is invoked at project setup, not
at runtime. There is no ambient relay process whose harness selection
should propagate. Making it an explicit flag at the install boundary
keeps the choice visible in the journal/history.

## What's explicitly NOT in this proposal

- **No harness-detection logic** in `relay install-skill`. The user
  picks. Auto-detect ("the current relay-v2 build supports pi, so
  install pi") sounds nice but couples install-skill to the
  orchestrator's harness selection — a non-goal until there is a real
  choice to disambiguate.
- **No second variant.** Adding `claude-code/` is a separate body of
  work (port the v1 skill *back* to its native Claude Code shape if
  needed, or fork from `pi/` and re-adapt). This proposal only sets
  the place where it would go.
- **No skill-catalogue refactor.** `engineering-team` is the only
  bundled skill; multi-skill discovery is a different problem.

## Acceptance criteria

When this proposal is implemented:

- [ ] `skills/engineering-team/pi/` contains today's contents
      (via `git mv`, preserving history).
- [ ] `skills/engineering-team/README.md` exists and explains the
      variant model.
- [ ] `skill_source_dir(name, harness)` resolves to the variant
      directory; unknown harness raises `FileNotFoundError` with a
      message listing available variants.
- [ ] `relay install-skill --harness pi` (and the no-flag default)
      install today's bytes to today's target path.
- [ ] `relay install-skill --harness claude-code` errors with a
      message naming the missing variant.
- [ ] `tests/cli/test_install_skill.py` covers both the default and
      `--harness` paths plus the unknown-harness failure.
- [ ] `tests/skills/test_skill_structure.py` locks the same six
      ADR-28 adaptations against `skills/engineering-team/pi/`.
- [ ] `docs/skills.md` updated: layout block, install-skill section,
      new variant model paragraph.
- [ ] An ADR is appended to `docs/decisions.md` (ADR-33?) recording
      the rename + variant-directory convention.
- [ ] CLAUDE.md "Current state" paragraph updated.

## Effort estimate

Small. ~½ day of focused work:

- File moves: minutes (`git mv`).
- Resolver + CLI flag: ~30 LoC + tests.
- Test path updates: mechanical.
- README.md: ~40 lines.
- Doc updates (`docs/skills.md`, CLAUDE.md, decisions.md ADR): ~½ hour.

No orchestrator, harness, REST, MCP, or schema changes. No
behaviour change for any existing run.

## Related

- ADR-28 — Phase 6 skill port (the six adaptations this proposal
  makes structurally visible).
- ADR-04 — harness isolation (the multi-harness commitment this
  proposal extends from skills/ down into bundled assets).
- `docs/skills.md` — current single-variant layout.
- `docs/spec.md` §12 — skill / sentinel grammar contract.
- [[parallel-iters-fanout-join]] — the bigger parallel-pi proposal;
  independent of this one (different layer of the stack).
