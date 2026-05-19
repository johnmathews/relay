# Phase 6 — engineering-team skill port

**Date:** 2026-05-19
**Branch:** `eng-phase-6-skill-port` (engineering-team cycle, FF-merged to `main`)

## What shipped

The relay-v2 port of v1's mature `engineering-team` skill, plus the
`relay install-skill` command. Skill + CLI only — no orchestrator,
REST, SSE, or MCP contract changed.

Files: `skills/engineering-team/` (11 docs: `SKILL.md`, four `phases/`,
six `references/`); `src/relay_v2/cli/{__init__,install_skill}.py`;
`install-skill` subcommand wired into `src/relay_v2/__main__.py`; hatch
`force-include` in `pyproject.toml`; `tests/cli/test_install_skill.py`
+ `tests/skills/test_skill_structure.py`; `docs/skills.md`; ADR-28 +
a `spec §12` Phase-6 implementation note.

## Faithful port, six deliberate adaptations (ADR-28)

The Phase-6 named risk is *prose-quality regression* (the v1 skill is
mature). Approach: port v1 prose and the sentinel **grammar**
verbatim; change only what relay-v2's reality forces, and only
deliberately:

1. **Single-session, no subagent dispatch** (spec §12, plan risk).
   v1's Task-tool "Engineer N / Product Owner" subagents → *analysis
   lenses the one session works in sequence*. relay's orchestrator has
   no `subagent_dispatch` handler yet (post-MVP); the phase structure
   and sentinels are preserved so it reintroduces cleanly later.
2. **`$RELAY_RUN_DIR` = `.relay/runs/<run_id>/`** everywhere; the v1
   `.engineering-team/runs/<utc>/` path fully removed (spec §3.3).
3. **Worktree is relay-provisioned** (ADR-13). Phase 3 *verifies* the
   orchestrator's worktree instead of running `git worktree add`; the
   v1 `current.txt` mirror is deleted (ADR-13 explicitly made that
   driver-side).
4. **Phase 4 inlines the gate.** No `/done` / `/merge-push` under pi
   (those are Claude Code slash-skills). Phase 4 runs their steps
   inline — sanity → CI-config → docs → full tests → security review
   → lint+types → journal → FF-merge → ask before push — keeping v1's
   intent (the unit loop does not run lint/types/security).
5. Sentinel source-doc pointers repoint to `docs/spec.md`.
6. Worked-example commands use relay-v2's `uv run` gate.

`tests/skills/test_skill_structure.py` locks all six so a careless
later edit fails the gate rather than silently regressing the port.

## Decisions discovered during the work (not pre-specified)

1. **Packaging.** `skills/` is repo-root, outside the `src/relay_v2`
   wheel. Added `[tool.hatch.build.targets.wheel.force-include]`
   mapping `skills` → `relay_v2/skills`; `skill_source_dir()` prefers
   the packaged path, falls back to the repo-root tree. Both the
   editable install (only mode used today) and a future wheel resolve
   a bundled copy. Rejected vendoring under `src/` (contradicts spec
   §12's stated path).
2. **Behavioral verification = documented manual step, not a
   `PI_INTEGRATION=1` test** (the judgment call flagged in the brief).
   The plan's acceptance (multi-iter pi run vs. the v1 fixture, "fixes
   the bug across iters", "dashboard renders cleanly") spawns real pi
   and is qualitative — identical profile to the three existing
   pi-gated e2e tests, and un-assertable as a deterministic unit
   test. Automated coverage is the CLI + structural suites; the full
   pi run is a procedure in `docs/skills.md`, attested in the journal
   like all pi e2e (ADR-24, CLAUDE.md). Recorded as ADR-28 §3.
3. **v1 skill-internal `~/.claude/skills/engineering-team/...` paths
   kept verbatim** — that is exactly `relay install-skill`'s default
   target, so the references stay valid; `--project` installs get a
   one-line substitution note in `phase-2-planning.md`.

## Verification

Gate green in the worktree: `uv run ruff check .` clean, `uv run mypy`
clean (**35 source files** — was 33; +`relay_v2.cli` package under
`--strict`), `uv run pytest` **183 passed, 3 skipped** (was 158; +25:
7 `install_skill` CLI tests, 18 structural-invariant cases; pi-e2e
still gated behind `PI_INTEGRATION=1`). Backend coverage 91%
(unchanged). Skill invariants additionally spot-checked by grep
(no `.engineering-team/runs`, no `Launch subagents in parallel`, no
v1 `git worktree add .claude/worktrees/eng-` instruction, no
`Run the /done skill` / `Run /merge-push` headers).

Behavioral pi acceptance (Phase-6 plan criterion) is **not** run here
by design (ADR-28 §3) — it is the documented manual procedure in
`docs/skills.md` against `~/projects/relay/relay-v1/fixtures/
eng-team-demo-seed` with the `inspect-eng-team-demo.sh` probe.

## Follow-ups

- Next coding work is **Phase 7** (OTel + Langfuse export, `docs/plan.md`).
- Subagent dispatch (`subagent_dispatch` signal + child runs) is the
  post-MVP feature that re-lights the skill's parallel-role lenses;
  the port preserved that seam.
- Run the `docs/skills.md` manual pi acceptance before declaring the
  MVP "done with Phase 6" end-to-end (plan.md "What done looks like").
