# Engineering-team skill (Phase 6)

Operational reference for the bundled `engineering-team` skill and
`relay install-skill`. Design authority: `spec.md §12`, ADR-14, ADR-28.
The skill is the relay-v2 port of v1's mature engineering-team skill;
it drives the evaluate → plan → develop → wrap-up cycle that relay
chains across fresh pi sessions.

## Layout

```
skills/engineering-team/          canonical source (repo root, spec §12)
├── README.md                     variant selector — human-readable,
│                                 never loaded by an agent (ADR-33)
└── pi/                           variant for relay + pi harness
    ├── SKILL.md                  router: reads RELAY_PHASE + RELAY_RUN_DIR
    ├── phases/
    │   ├── phase-1-evaluation.md
    │   ├── phase-2-planning.md
    │   ├── phase-3-development.md
    │   └── phase-4-wrap-up.md
    └── references/
        ├── sentinels.md          verbatim v1 grammar (spec §12)
        ├── team-structure.md     roles as single-session lenses
        ├── workflows.md
        ├── worktree.md           relay provisions it; skill does not
        ├── discussion.md
        └── general-guidelines.md
```

Each harness gets its own subdirectory; today only `pi/` exists. A
future second variant would land as a sibling (e.g. `claude-code/`) —
see ADR-33 for the rationale. The tree lives at the **repo root**,
outside the `src/relay_v2` wheel package. A hatch `force-include`
(`pyproject.toml`) maps the whole `skills/` tree into built wheels as
`relay_v2/skills/` so new variant subdirectories are automatically
bundled; editable/source installs (the only mode used today) resolve
the repo-root tree directly.

## `relay install-skill`

```
relay install-skill                       # → ~/.claude/skills/engineering-team/ (pi)
relay install-skill --project PATH        # → PATH/.claude/skills/engineering-team/
relay install-skill --force               # overwrite, backing the old copy up first
relay install-skill --harness pi          # explicit variant selection (default)
relay install-skill --harness claude-code # errors — variant not present today
```

Refuses to clobber an existing install unless `--force`; with `--force`
the existing directory is moved to `engineering-team.bak-<utcstamp>`
before the fresh copy is written. Source resolution lives in
`src/relay_v2/cli/install_skill.py:skill_source_dir`.

`--harness <name>` selects the variant subdirectory under
`skills/engineering-team/`; the install target path is unchanged
(no `<name>` suffix at the destination) because the agent reads
`engineering-team`, not `engineering-team-<harness>`. An unknown
harness errors with a message listing the available variants. The
variant-selector `README.md` (one level above the variant directory)
is copied alongside the variant contents so humans inspecting the
install can see what was deployed; agents never load it.

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
dispatch within the skill itself**. v1's "Engineer N / Product Owner"
subagents are *analysis lenses the single session works in sequence*.
The orchestrator-level fanout-join arc (9a–9f) is live, but the
bundled engteam skill does not currently emit `[[engteam:fanout]]` —
plural subagent parallelism is opt-in for future skills / variant
phases. ADR-28's phase structure and sentinel contract leave room
for the engteam skill to adopt fanout later without restructuring.

**14d — pause-for-review wired into Phase 2.** The engteam Phase-2
Step-4 closing sentinel emits `review_path="improvement-plan.md"`
alongside `id` and `question` on the same line. The dashboard's
inline review pane (14c) lets the operator edit
`improvement-plan.md` before resuming; the resumed iter re-reads it
in full (the Step-4 `prompt-start`/`prompt-end` body has the
load-bearing "the user may have edited it" instruction so the
ADR-20 fresh-context-per-iter mechanism picks up the edits). Phase
2 also gains a blockquote cross-link to `references/fanout.md`
(14e — closes the deferred 9e follow-up).

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
uv run pytest` (342 passed, 3 pi-e2e skipped; ADR-28 — count includes
the 9a–9g + 14a–14f post-MVP additions).

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
