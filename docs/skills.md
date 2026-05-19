# Engineering-team skill (Phase 6)

Operational reference for the bundled `engineering-team` skill and
`relay install-skill`. Design authority: `spec.md §12`, ADR-14, ADR-28.
The skill is the relay-v2 port of v1's mature engineering-team skill;
it drives the evaluate → plan → develop → wrap-up cycle that relay
chains across fresh pi sessions.

## Layout

```
skills/engineering-team/          canonical source (repo root, spec §12)
├── SKILL.md                      router: reads RELAY_PHASE + RELAY_RUN_DIR
├── phases/
│   ├── phase-1-evaluation.md
│   ├── phase-2-planning.md
│   ├── phase-3-development.md
│   └── phase-4-wrap-up.md
└── references/
    ├── sentinels.md              verbatim v1 grammar (spec §12)
    ├── team-structure.md         roles as single-session lenses
    ├── workflows.md
    ├── worktree.md               relay provisions it; skill does not
    ├── discussion.md
    └── general-guidelines.md
```

The tree lives at the **repo root**, outside the `src/relay_v2` wheel
package. A hatch `force-include` (`pyproject.toml`) maps it into built
wheels as `relay_v2/skills/` so an installed wheel still carries it;
editable/source installs (the only mode used today) resolve the
repo-root tree directly.

## `relay install-skill`

```
relay install-skill                 # → ~/.claude/skills/engineering-team/
relay install-skill --project PATH  # → PATH/.claude/skills/engineering-team/
relay install-skill --force         # overwrite, backing the old copy up first
```

Refuses to clobber an existing install unless `--force`; with `--force`
the existing directory is moved to `engineering-team.bak-<utcstamp>`
before the fresh copy is written. Source resolution lives in
`src/relay_v2/cli/install_skill.py:skill_source_dir`.

## How relay drives the skill

- The orchestrator builds the per-iter preamble (`orchestrator/
  preamble.py`): `RELAY_RUN_DIR` (always) and `RELAY_PHASE` (once a
  `phase-start` has been seen). `RELAY_RUN_DIR` is
  `<project_root>/.relay/runs/<run_id>/` — the artifacts directory, a
  **sibling** of `.relay/worktrees/<run_id>/`, never nested in it
  (spec §3.3).
- relay provisions the per-run git worktree on branch `relay/<run_id>`
  (`orchestrator/lifecycle.py:provision_workspace`, ADR-13). The skill
  **verifies** it in Phase 3; it never runs `git worktree add`.
- The skill emits text sentinels (`[[engteam:<verb> ...]]`); the
  harness's `text_sentinels` strategy parses them and the loop
  continues / stops / pauses accordingly. Grammar: `references/
  sentinels.md` (unchanged from v1, spec §12).

## Single-session MVP

relay-v2's MVP runs **one long session per phase, no subagent
dispatch**. v1's "Engineer N / Product Owner" subagents are *analysis
lenses the single session works in sequence*. Subagent parallelism is
a post-MVP relay feature (a `subagent_dispatch` signal the orchestrator
does not yet handle); the phase structure and sentinel contract are
written so it can be reintroduced without restructuring (ADR-28).

## Verification

**Automated (CI, deterministic):**

- `tests/cli/test_install_skill.py` — source resolution, project /
  home targets, `--force` backup, exit codes, arg wiring.
- `tests/skills/test_skill_structure.py` — locks the deliverable shape
  and the six ADR-28 port adaptations (file set, frontmatter, the
  seven-verb grammar + prompt markers, `.relay/runs` path migration,
  relay-provisioned-worktree wording, single-session adaptation, the
  inlined-gate / no-`/done` Phase-4 note).

Run with the project gate: `uv run ruff check . && uv run mypy &&
uv run pytest` (183 passed, 3 pi-e2e skipped; ADR-28).

**Behavioral (manual, pi-gated — ADR-28).** The end-to-end acceptance
(plan.md Phase 6) spawns real pi and is qualitative, so it is a manual
procedure, gated exactly like the three `PI_INTEGRATION=1` e2e tests
(non-deterministic, multi-minute, needs the Max-subscription pi auth):

1. Seed the v1 demo fixture (deliberately broken task-tracker, e.g.
   `factorial(5)` returns 24):
   ```bash
   ~/projects/relay/relay-v1/fixtures/eng-team-demo-seed/reset.sh
   ```
2. `relay install-skill` (or `--project` the fixture), then start a
   relay run against the fixture root with an "evaluate, plan, and fix
   the bugs" prompt and `PI_AGENT_SDK=1` set.
3. Watch the run in the dashboard: confirm a clean multi-phase
   timeline — `phase-start` evaluation → planning (`pause-for-input`
   gate) → development (`unit-start`/`unit-done` per work unit) →
   wrap-up (`done`), with `evaluation-report.md` and
   `improvement-plan.md` rendering in the Artifacts pane.
4. Inspect the result with the v1 probe (survives `reset.sh` and
   worktree teardown):
   ```bash
   ~/projects/relay/relay-v1/examples/inspect-eng-team-demo.sh <fixture-root>
   ```
   Expect: the seeded bug fixed, the fixture's test suite green, the
   per-run branch merged, the journal entry written.

Record the outcome in the dev journal; this is the project-wide
convention for pi e2e (ADR-24, CLAUDE.md) — attested, not CI-gated.
