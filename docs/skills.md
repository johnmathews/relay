# Engineering-team skill

Operational reference for the bundled `engineering-team` skill and how
relay delivers it to pi. Design authority: `spec.md §12`, ADR-14,
ADR-28 (original Phase-6 install model), **ADR-44 (current delivery
model — supersedes the install-skill command).**

The skill is the relay port of v1's mature engineering-team skill;
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
        ├── sentinels.md          verbatim v1 grammar (spec §12) — also
        │                         documents pause-attribute review_paths
        │                         (14b/14f) and fanout markers (9b)
        ├── team-structure.md     roles as single-session lenses
        ├── workflows.md
        ├── worktree.md           relay provisions it; skill does not
        ├── fanout.md             phase-level fanout reference (9e/14e
        │                         follow-up — cross-linked from phase-1/2/3)
        ├── discussion.md
        └── general-guidelines.md
```

Each harness gets its own subdirectory; today only `pi/` exists. A
future second variant would land as a sibling (e.g. `claude-code/`) —
see ADR-33 for the rationale. The tree lives at the **repo root**,
outside the `src/relay` wheel package. A hatch `force-include`
(`pyproject.toml`) maps the whole `skills/` tree into built wheels as
`relay/skills/` so new variant subdirectories are automatically
bundled; editable/source installs (the only mode used today) resolve
the repo-root tree directly.

## How relay delivers the skill (ADR-44)

Pi has a first-class skill system (`pi --skill <path>` is repeatable
and accepts a file or directory containing `SKILL.md` + sibling
files). Relay's harness uses this directly:

- **`src/relay/harness/skills.py:bundled_skill_dir()`** is the single
  resolver — tries the wheel-bundled location first
  (`<site-packages>/relay/skills/engineering-team/pi`), then falls
  back to the repo-root source layout
  (`<repo>/skills/engineering-team/pi`). Either is acceptable; raises
  `FileNotFoundError` if neither is present (broken install).
- **`Settings.pi_skill_paths`** (`src/relay/config.py`) is a derived
  property whose default is `[bundled_skill_dir()]`. Override via the
  colon-separated env var `RELAY_PI_SKILLS`. Setting it to the **empty
  string** explicitly opts out — pi then only sees auto-discovered
  skills (see below).
- **`PiHarness._build_argv`** (`src/relay/harness/pi.py`) appends
  one `--skill <abs-path>` pair per configured path on every spawn.
  The skill content goes into pi's system prompt via the SDK's
  `formatSkillsForPrompt`; the `phases/` and `references/` siblings
  stay readable on disk for pi's `Read` tool — lazy load is preserved.

**Pi's auto-discovery stays on.** Pi independently looks in
`<cwd>/.pi/skills/` (project scope) and `~/.pi/agent/skills/` (user
scope). Explicit `--skill` injection is **additive**, not exclusive
— operators can drop a customised skill into either auto-discovered
location without touching relay. The bundled engteam skill is
guaranteed to load regardless because relay always injects it.

> **Historical note.** Phase 6 (ADR-28) shipped a `relay install-skill`
> command that copied the skill into `<target>/.claude/skills/`. That
> path is a **Claude Code** discovery root, not a pi one — pi reads
> `.pi/skills/`, not `.claude/skills/`. The install command was silently
> inert: pi never saw the skill. ADR-44 records the discovery and the
> switch to `--skill` injection; `relay install-skill` was deleted
> outright on 2026-05-25.

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

relay's MVP runs **one long session per phase, no subagent
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

The harness-side grammar supports a **plural** `review_paths` storage
shape (14f / ADR-41): repeating the `review_path="..."` attribute on
the same line declares multiple reviewable artifacts, and the
dashboard renders one tab per path. The bundled engteam Phase-2
template emits exactly one — plural is opt-in for future skills.

## Verification

**Automated (CI, deterministic):**

- `tests/harness/test_pi_skills.py` — bundled-skill resolver works in
  editable + packaged installs; `PiHarness._build_argv` emits one
  `--skill <path>` pair per configured path; `RELAY_PI_SKILLS=` opts
  out; colon-separated env values override.
- `tests/skills/test_skill_structure.py` — locks the deliverable shape
  and the six ADR-28 port adaptations (file set, frontmatter, the
  seven-verb grammar + prompt markers, `.relay/runs` path migration,
  relay-provisioned-worktree wording, single-session adaptation, the
  inlined-gate / no-`/done` Phase-4 note).

Run with the project gate: `uv run ruff check . && uv run mypy &&
uv run pytest`.

**Behavioral (manual, pi-gated — ADR-28).** The end-to-end acceptance
spawns real pi and is qualitative, so it is a manual procedure, gated
exactly like the three `PI_INTEGRATION=1` e2e tests (non-deterministic,
multi-minute, needs the Max-subscription pi auth):

1. Seed the v1 demo fixture (deliberately broken task-tracker, e.g.
   `factorial(5)` returns 24):
   ```bash
   ~/projects/relay/relay-v1/fixtures/eng-team-demo-seed/reset.sh
   ```
2. Start `relay serve`. No skill install step needed — the bundled
   skill is injected automatically. Open the dashboard, register the
   fixture as a project, and start a run against it with an
   "evaluate, plan, and fix the bugs" prompt prefixed `/engineering-team`.
   `PI_AGENT_SDK=1` is injected by the harness.
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
