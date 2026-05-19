# Pre-Phase-3 hardening

An `engineering-team` evaluate → plan → develop cycle run against the
Phase 0–2 codebase before starting Phase 3 (REST + persistence). The
evaluation found the foundation sound and on-plan, but flagged a real
resume bug, a startup resource leak, broken coverage tooling, and an
unenforced pi pin. Nine work units (W1–W9) closed those plus the test
gaps that Phase 3's HTTP surface will lean on. Run artifacts (evaluation
report + improvement plan) live under
`.engineering-team/runs/manual-20260519T123237Z/`.

## What changed

- **W1 — resume-at-max_iters bug (ADR-22).** A run that paused on its
  last budgeted iter (`paused.seq == max_iters`) ended `failed/max_iters`
  the instant it was resumed, discarding the human's answer:
  `while seq < max_iters` was immediately false. `run_loop` now bounds
  on `effective_max = max(ctx.max_iters, seq + 1)` — a fresh run is
  unchanged (`start_seq == 0`), a resumed run gets ≥1 post-answer iter.
  Decision + rejected alternatives recorded as **ADR-22**; spec.md §6.2
  documents the effective-cap contract. Regression test added.
- **W2 — leaked sync engine.** `RelayCore.start()` called `init_db()`
  and discarded the returned sync engine (connection pool never
  disposed); `app.py` already disposes its own. `start()` now disposes
  the bootstrap engine immediately — RelayCore only needs schema
  *existence* and owns its async engine. Schema bootstrap is preserved
  (orchestrator tests depend on `start()` creating it), so no test
  impact.
- **W3 — coverage tooling.** `coverage`/`pytest-cov` were absent despite
  the global policy; `--source=src` reported a false 0% (the editable
  install resolves as `relay_v2`, not `src`). Added
  `coverage[toml]`/`pytest-cov`/`pytest-asyncio` to dev deps, wired
  `--cov=relay_v2` + `[tool.coverage.*]`. **Baseline 90% → 91%** after
  W5–W8 (the meaningful gain is invariant paths, not the number).
- **W4 — pi pin (OQ-5).** Added `.tool-versions` (`pi 0.74.0`) +
  `Settings.pi_expected_version`. `PiHarness` runs a best-effort once
  `pi --version` probe on first spawn and logs a non-fatal warning on
  mismatch (`pi_version_mismatch_warning`, pure + unit-tested). OQ-5
  resolved in spec.md §13; README documents the pin.
- **W5 — EventStore integrity tests.** New `tests/orchestrator/
  test_events.py`: `_next_seq` cold-cache reseed on simulated restart
  (UNIQUE(run_id,seq) safety), `_truncate_result` over/under cap, the
  `store_harness_event` tool branches and intentional drops.
- **W6 — RelayCore guards + concurrency.** Tests for unknown-project
  start, unknown-run cancel no-op, not-paused resume, duplicate-resume
  guard, unknown-run `wait_for_run` KeyError, two concurrent runs with
  isolated seqs, and aclose cancelling an in-flight run.
  `test_cancel_run` is now race-free: `ScriptedHarness` exposes a
  `blocked` event so the test awaits the exact hung moment instead of
  `sleep(0.2)`.
- **W7 — parser/lifecycle/preamble gaps.** `detect_in_text` now
  exercised for all seven verbs (`unit_start`/`unit_abandoned` were
  unreached) plus dual-close (done wins) and done-with-markers
  (MarkerError). New `test_lifecycle.py` (register_project idempotent,
  `provision_workspace` git-success **and** fallback) and
  `test_preamble.py`. The two `test_session_resume_run*.jsonl` fixtures
  are finally asserted (shared session id).
- **W8 — phase_start event + resume cwd guard.** A turn carrying
  `phase-start` *and* a terminal signal never recorded a
  `signal_emit{phase_start}` (detect returned the terminal first), so
  Phase 4 timeline/replay would miss the transition. The carry-forward
  path now emits it exactly once, guarded by an
  `_IterOutcome.phase_start_emitted` flag against duplication.
  `resume_run` now raises if the project row is gone (was a silent
  `Path()` → process-CWD fallback) — and the check moved *before* the
  status flip / `pause_resolved` append so a failure leaves no
  half-resumed state.
- **W9 — docs.** README status (Phases 0–2 + hardening; ADR count
  17→22), CLAUDE.md CLI list scoped to implemented commands, spec §6
  pseudocode annotated as illustrative (points at the `_drive_iter`/
  `_finish_iter` split + ADR-22 bound), OQ-4 resolved, `docs/archive/`
  convention established.

## Decisions

- **ADR-22.** Resume guarantees forward progress: rejected
  "pause doesn't count against the cap" (harder to reason about across
  multiple pauses) and "succeed-and-stop" (drops the answer). Chose the
  minimal `max(max_iters, start_seq+1)` bound — one extra iter per
  resume, intentional and bounded.
- **W2 shape.** Disposed the bootstrap engine rather than removing the
  `init_db` call, because orchestrator tests construct `RelayCore`
  directly and rely on `start()` creating the schema (they don't go
  through `create_app`'s lifespan). Removing the call would have broken
  the suite; `app.py` was deliberately left untouched.
- **pi pin enforcement is advisory, not fatal.** A hard abort on version
  drift is wrong for a single-user MVP where the mapper degrades
  gracefully on additive schema changes; the hard pin is the committed
  `.tool-versions`, the runtime check just warns.

## Discovered during development

- Coverage reported a false 0% with `--source=src`: the hatchling
  editable install (`_editable_impl_relay_v2.pth` → `src/`) means the
  package imports as `relay_v2`. Fixed by `source = ["relay_v2"]`.
- The repo's security hook fires a Node-flavored subprocess warning on
  Python's `asyncio` no-shell subprocess spawn — a false positive; that
  is the same safe exec-form (argv list, no shell) already used by
  `PiHarness.spawn`.
- 13–14 `ResourceWarning`s now surface (pytest-cov enables warning
  capture): test helpers (`_read`, `_store`, `_sm`) don't dispose
  throwaway engines. Pre-existing pattern (`test_loop._read`), benign,
  consistent with existing code — left as-is; a future test-fixture
  cleanup could dispose them.

## Verification

- `uv run pytest` → **99 passed, 1 skipped** (the skip is the gated
  `PI_INTEGRATION=1` harness e2e). Baseline at session start: 70 passed.
- `uv run ruff check .` clean; `uv run mypy` (strict) clean — 21 files.
- Coverage **91%** (`htmlcov/` generated); previously-dead invariant
  paths (EventStore reseed/truncation, RelayCore guards, all sentinel
  verbs, worktree git-success) now covered.

## Follow-ups (out of scope here)

- Crash-recovery `--session` resume path is still untested and the
  `--session` UUID-vs-path question (suspected pi-flag issue) is open —
  a future spike, not Phase 3 critical path.
- Test-fixture engine disposal to silence the `ResourceWarning`s.
- Phase 3 proper: REST routes + SSE over `RelayCore` (no new write
  paths — routes call existing service methods; the guard tests added
  in W6 define the expected HTTP error mapping).
