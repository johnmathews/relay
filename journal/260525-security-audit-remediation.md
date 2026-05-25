# 260525 — Security audit remediation (W1–W8)

Single multi-unit improvement plan executed end-to-end against the
2026-05-25 Phase-1 evaluation report (security audit + documentation
sweep). Eight findings, eight work units, one cumulative commit on
`relay/20260525-160758-11ce`.

Plan: `.relay/runs/20260525-160758-11ce/improvement-plan.md`
Source report: `.relay/runs/20260525-160758-11ce/evaluation-report.md`

## What shipped, per unit

- **W1 — D-1/D-8 docs.** README's MCP DNS-rebinding claim reworded to
  reflect ADR-12's MVP envelope (the localhost bind is the protection;
  DNS-rebinding allow-list was an overclaim). `docs/api.md` gained a
  browse-endpoint warning paragraph: the host-side browser is unsandboxed
  and the operator is the only access control, so binding off-localhost
  exposes the host filesystem read-only via `/api/system/browse`.

- **W2 — D-2/D-3/D-6 count refresh.** Headline counts in `README.md`,
  `CLAUDE.md`, and `docs/plan.md` updated to the post-work numbers
  (367 backend + 3 pi-e2e gated, 232 frontend, 46 ADRs, 39 source
  files, 94→95% coverage). No new ADRs were added by this plan;
  the ADR count is a re-count not a change.

- **W3 — S-4/S-6 test gate.** `tests/observability/test_otel_export.py`
  was hard-asserting the system env so a developer with
  `RELAY_LANGFUSE_*` set in their shell saw the misconfig path test
  fail. Now monkeypatches the three env keys to `None` inside the
  test. Restores a green local gate regardless of operator env.

- **W4 — S-2 MCP read-artifact guards.** `relay__read_artifact` now
  enforces `MAX_FILE_BYTES` + binary-sniff guards by importing the
  shared `BINARY_SNIFF_BYTES`, `MAX_FILE_BYTES` constants from
  `api/files.py`. Same defense-in-depth as `serve_file` — a 10MB
  binary artifact would have streamed straight into the MCP client.

- **W5 — S-3 fanout width cap.** `FanoutPayload` parser now rejects
  any list longer than `MAX_FANOUT_CHILDREN_HARD_CAP = 32` at
  validate time. A new operator-tunable
  `settings.max_fanout_width` (default 8, env
  `RELAY_MAX_FANOUT_WIDTH`) is enforced in
  `RelayCore._dispatch_children`. A fanout over the soft cap routes
  through the audited `_apply_result → failed` path (parent run
  fails with `max_fanout_width` in `run_ended.summary`; zero child
  worktrees provisioned). The hard cap is parser-enforced regardless
  of config so a malformed agent emission is rejected at parse time.

- **W6 — S-1 startup host warning.** `relay serve` now prints a
  multi-line WARNING to stderr when `RELAY_HOST` is not one of
  `127.0.0.1` / `localhost` / `::1`. **Warn, do not refuse** — same
  precedent as `harness/pi.py`'s pi-version-mismatch path. The
  warning enumerates the specific exposures (browse, runs, SSE, MCP
  reachability) so the operator can make an informed call.

- **W7 — S-5 delete_project cascade.** `delete_project` was leaving
  orphan rows in `runs`, `events`, `iters`, and `prompts` because
  the schema has no FK cascade (ADR-17 hand-rolled). Rewrote to walk
  `Run.project_id == project_id` and call `delete_run` on each (the
  audited path that already cascades events + iters + child runs),
  then deletes project-scoped `Prompt` rows
  (`Prompt.project_id == project_id` — the FK is nullable so
  project-global prompts are unaffected), then the project row. An
  active run (`running` / `awaiting_children`) raises `ValueError`,
  mapped by the REST adapter to **409**; 404 unknown; 204 success.

- **W8 — D-4/D-5 spec.md §7/§11 fill-ins.** `docs/spec.md` §7 gained
  the missing `DELETE /api/runs/:id` and `GET /api/system/browse`
  endpoints; §11 picked up `RELAY_MAX_FANOUT_WIDTH` (W5) and
  `RELAY_PI_SKILLS` (ADR-44, was undocumented).

## Non-goals preserved

Per the plan, the following were explicitly out of scope and
confirmed not crept-into-scope during the security review:

- No auth/RBAC, no multi-user (ADR-12 envelope unchanged).
- No FK-cascade migration / `PRAGMA foreign_keys=ON` — W7 uses the
  explicit-cascade pattern matching `delete_run`.
- No new ADRs — every fix is a gap against an existing contract.
  W6's "warn-not-refuse" is consistent with the existing
  pi-version-mismatch precedent in `harness/pi.py`, so no new ADR.
- No on-disk-orphan cleanup (W7 cascade affects DB only;
  per-project artifact dirs under `<project_root>/.relay/runs/`
  stay until manually `rm -rf`'d, matching `delete_run`'s
  "DB-only" contract).
- No CI assertion for count drift (the audit flagged the drift, not
  the process).
- No acceptance-testing-phase work.

## Gate

Phase 4 Step 1 (mandatory, inline — the unit loop deliberately
skipped lint, types, and a security pass).

- `uv run pytest --cov=src/relay_v2` → **367 passed, 3 skipped, 95%**
  (was 359 + 3 skipped at run start; +8 tests across W3/W4/W5/W6/W7
  scenarios; +1pp coverage from the new branches).
- `uv run ruff check .` → clean.
- `uv run mypy` → clean (39 source files).
- `frontend/ npx vitest run` → **232 passed** (frontend untouched).

Main advanced 3 commits during this run (wizard preview/options +
UTC-tagging fix); rebased onto main cleanly with no conflicts.
Post-rebase the absolute counts in CLAUDE.md and README.md were
bumped to **371 backend / 234 frontend / 40 source files** to stay
self-consistent with the rebased tree. Re-running the gate after
rebase: all green.

## Security pass over the diff

A deliberate `git diff` review surfaced **no security findings** — the
changes are themselves defensive (W4, W5, W6, W7). Notes from the
pass:

- W6 stderr warning uses `.format(host=settings.host)` against a
  literal template, no f-string concat — no injection surface; print
  goes to stderr only, no fs / network side effect.
- W7's cascade uses parameterised SQLAlchemy
  (`sql_delete(Prompt).where(Prompt.project_id == project_id)`) and
  the project-id input is an `int` route param — no injection.
- W7 deliberately does not cross-project-cascade: children are
  always same-project as their parent (`provision_workspace`
  branches off the parent's worktree HEAD), so walking
  `Run.project_id == project_id` is complete.
- W4 keeps the `errors="replace"` decode (matches `serve_file`
  semantics — best-effort text view for MCP callers).

## Plan-drift / gotchas discovered during the gate

Three independent **duplicate-block** issues surfaced when the
mandatory lint + types gate ran (the unit loop had been using
`pytest --no-cov` only):

- `tests/orchestrator/test_sentinels_fanout.py` — W5's hard-cap
  test was pasted twice; ruff F811.
- `tests/orchestrator/test_fanout_dispatch.py` — W5's soft-cap
  test was pasted twice; ruff F811.
- `src/relay_v2/config.py` — W5's `max_fanout_width` field was
  declared twice in `Settings`; mypy `no-redef`.
- `src/relay_v2/core.py` — W5's width-limit `if/raise` block was
  inlined twice in `_dispatch_children`; lint missed it (the second
  `if` is unreachable but syntactically valid), security review
  caught it.

All four were exact duplicates with no behavioural difference;
deduped in the wrap-up phase before merge. The lesson is one for
future unit loops: re-running just `pytest --no-cov` between units
hides this class of mistake. The wrap-up gate's
`ruff + mypy + security review` triple is the safety net by design.

## Follow-ups (not done in this plan)

- A CI guard for headline-count drift (the audit's D-2/D-3/D-6
  found this drift; W2 fixed the symptom not the cause).
- A one-shot script to clean up orphaned rows in existing
  dev/test DBs that pre-date W7's cascade. The plan declared this
  out of scope; it remains so.
- Future `_dispatch_children` callers should rely on the parser's
  hard cap as well as the dispatch-time soft cap — both are now
  in place and exercised by tests.
