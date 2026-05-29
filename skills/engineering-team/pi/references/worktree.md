# Worktree and linter setup

> Loaded by phase docs (especially Phase 3) when worktree, linter
> detection, or working-directory invariants are relevant.

## Working Directory

The target repo is the current working directory unless the user specifies another path.

**Git repo check:** Before starting any work, check whether the project is a git repo.

- **If it IS a git repo:** Check whether it has a remote on GitHub under the `johnmathews` account. If not,
  ask the user to confirm before creating one.
- **If it is NOT a git repo:** Ask the user before initializing one. Some projects (notes, config directories,
  documentation collections) may not need git. If they decline, work directly
  in the directory — Phase 4's merge step becomes a simple "ask the user if they want to commit" instead.
- **If the repo has zero commits** (freshly initialized): Create an initial commit (e.g., `git add -A &&
  git commit -m "Initial commit"`) before any worktree could exist, since git worktrees require at least
  one commit.

### Worktree Isolation — relay provisions it, the skill does NOT

This is the load-bearing v2 difference (ADR-13, ADR-14). In v1 the
Phase-3 skill created the worktree itself (`EnterWorktree` / `git
worktree add`). **In relay the orchestrator provisions the per-run
worktree before your session starts** and runs you with your working
directory already inside it:

- relay creates `<project_root>/.relay/worktrees/<run_id>/` on a
  per-run branch `relay/<run_id>` and sets your `cwd` there (spec.md
  §3.3, §6).
- The artifacts directory `$RELAY_RUN_DIR`
  (`<project_root>/.relay/runs/<run_id>/`) is a **sibling** of the
  worktree, never nested inside it. Phase artifacts
  (`evaluation-report.md`, `improvement-plan.md`, `discussions/`) go
  there so they survive worktree teardown and stay visible to the
  dashboard's Artifacts pane across phases.

**Therefore, in Phase 3 you do NOT run `git worktree add` and you do
NOT create a nested worktree.** Your first action is to *verify* you
are in the relay-provisioned worktree, not to create one:

```bash
git rev-parse --show-toplevel   # should end in /.relay/worktrees/<run_id>
git branch --show-current       # should be relay/<run_id>
```

If `git rev-parse --show-toplevel` is the project root (no worktree),
relay degraded worktree provisioning — this happens when the project is
not a git work tree (ad-hoc dirs, fixtures). In that case work directly
in the project root; Phase 4's merge step degrades to a plain commit
(see `../phases/phase-4-wrap-up.md`). Do not try to create a worktree
yourself to "fix" this — relay owns workspace provisioning, and a
skill-created nested worktree breaks the dashboard's run resolver and
the Phase-4 merge.

There is no `current.txt` mirror step in v2 — run-dir resolution is
driver-side (relay tracks `run_id` in its event store; the dashboard
resolves artifacts from there, ADR-13).

All work (evaluation reports go to `$RELAY_RUN_DIR/`; code changes,
tests, docs, journal entries go in the worktree) happens without
touching `main`; relay's per-run branch holds it until Phase 4 merges.

All project-facing documentation goes in `/docs/`. The development journal goes in `/journal/` with filenames
like `250321-descriptive-name.md` (YYMMDD format). Create these directories if they don't exist. These
paths are relative to the worktree root (which relay set as your cwd).

### Linter Setup

**New projects (you just ran `git init`):** Set up a linter as part of project initialization. Choose the
appropriate linter for the project's primary language:
- **Python:** `ruff` (configure in `pyproject.toml` with `[tool.ruff]` section)
- **JavaScript/TypeScript:** `eslint` (with a flat config `eslint.config.js`)
- **Go:** `golangci-lint` (with `.golangci.yml`)
- **Rust:** `clippy` is built-in, but add a `clippy.toml` if custom rules are needed
- **Ansible/YAML:** `ansible-lint` (with `.ansible-lint`)

Use sensible defaults — don't over-configure. The goal is a working linter with reasonable rules that the
user can customize later. Add a lint command to the `Makefile` if one exists (or create a simple one).

**Existing projects:** Check for a linter during Phase 1 (see `../phases/phase-1-evaluation.md`). If none is found, ask the user
whether they'd like one set up before proceeding with the evaluation.
