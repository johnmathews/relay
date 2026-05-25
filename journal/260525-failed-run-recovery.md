# 260525 — failed-run recovery: agent reset-and-exit in wrap-up

A real-world failure mode worth recording, because the recovery was
non-obvious and the underlying behaviour is a candidate for a future
sentinel-grammar improvement.

## What happened

Run `20260525-160758-11ce` (`/engineering-team do a security audit and
documentation sweep`) finished iters 1–4 cleanly:

| iter | phase       | signal  |
| ---- | ----------- | ------- |
| 1    | (eval setup) | pause  |
| 2    | evaluation  | pause   |
| 3    | planning    | handoff |
| 4    | development | pause   |

Iter 4 closed with a `pause-for-input` asking how to land the W1–W8
remediation commit `bff8dff` onto main given a pre-existing
uncommitted WIP file (`frontend/src/components/runs/TimelinePane.vue`)
in the main worktree. The operator answered. Iter 5 (wrap-up) ran for
~2 min and ended with `agent_end_no_signal` — the agent finished its
turn without `[[engteam:done]]` / `[[engteam:handoff]]` /
`[[engteam:pause-for-input]]`.

## What the agent actually did in iter 5 (reflog)

```
HEAD@{0}: reset: moving to main
HEAD@{1}: commit: feat(security-audit): W1–W8 remediation sweep   ← bff8dff
HEAD@{2}: reset: moving to main
HEAD@{3}: commit (amend) …
HEAD@{4}: commit …
HEAD@{5}: commit (amend) …
HEAD@{6}: commit (amend) …
HEAD@{7..9}: rebase (finish/pick/start)
HEAD@{10}: commit …
HEAD@{11}: reset: moving to HEAD
```

The wrap-up agent did a long rebase-amend dance, landed `bff8dff` on
the branch, then **reset HEAD back to main** and exited with no
sentinel. The commit was unreachable except via reflog; the
working-tree changes that had been committed in `bff8dff` were left as
17 dirty files (`git diff bff8dff -- .` returned 0 — byte-identical).

## Why the reset is a bug (or at least a smell)

ADR-30 splits engteam phase-5 wrap-up into automated vs manual; the
manual step is "operator merges to main". The wrap-up agent's job is
to leave the branch in a mergeable state, **not** to unwind its own
commit. The `git reset --hard main` was almost certainly the agent
attempting to "clean up" because it couldn't FF-merge (main had moved
two commits ahead — `96307da` + `b7bd5f6` — between when the agent
forked at `426f6da` and when iter 5 ran). A correct wrap-up would have
either (a) rebased onto current main and paused asking the operator to
FF-merge, or (b) just left the branch alone and paused. Resetting is
strictly worse than doing nothing.

The agent exiting silently (no sentinel) means the orchestrator can't
distinguish "I'm done, please merge" from "I gave up and undid my
work". `agent_end_no_signal` is the catch-all and surfaces in the
dashboard as a red error card with a generic message about token
budgets / API failures — which is misleading when the actual cause is
an agent voluntarily relinquishing without grammar.

## Recovery procedure (worked, ~2 min, no data loss)

1. `cd <worktree>`
2. `git reset --hard bff8dff` — restored branch ref + working tree;
   content was already on disk, so this is a ref-update with a no-op
   checkout.
3. `git rebase main` — clean (W1–W8 was backend/docs/tests; the two
   new main commits were frontend dashboard only).
4. `cd <main-worktree> && git merge --ff-only <branch>` — FF succeeded
   to `e818cac` (rebased SHA); `TimelinePane.vue` WIP untouched.
5. `git worktree remove <path>; git branch -D <branch>` — tidy.

Run row + artifacts dir under `.relay/runs/20260525-160758-11ce/`
left in place — dashboard's clear-history UI is the right tool for
that, not a manual `sqlite3 DELETE`.

## What this suggests for relay itself

Not action items, just observations:

1. **Reflog as a structural safety net is enough for this class of
   bug.** Even when the agent does the wrong thing, nothing was lost
   — `git` preserved the commit and the worktree preserved the
   content. The orchestrator doesn't need to monitor what the agent
   does to the filesystem.

2. **A "soft fail" sentinel might be worth considering.** Right now
   the grammar has `done` / `handoff` / `pause-for-input` / `fanout`.
   There's no way for an agent to say "I tried, I'm exiting cleanly,
   the work is in this state, but I don't have a clear next-step
   recommendation". The agent in iter 5 had nothing reasonable to
   emit, so it emitted nothing — and the resulting
   `agent_end_no_signal` error message is wrong (it suggests
   token-budget / API failure, which wasn't the actual cause).
   Counter-argument: a soft-fail sentinel just gives agents a way to
   ergonomically give up. The current pressure to emit
   `pause-for-input` with a sensible question is probably correct
   behaviour. Not changing anything.

3. **The wrap-up phase prompt could be tightened.** "Merge to main"
   is the canonical manual step (ADR-30). The agent's prompt for
   wrap-up should probably forbid `git reset` explicitly and require
   that any merge complication ends in a pause, not an unwind.

## State at end of session

- `main` at `e818cac` (W1–W8 sweep landed, **not pushed** — ADR-12
  default no).
- Branch + worktree for `20260525-160758-11ce` removed.
- `TimelinePane.vue` WIP in main still uncommitted (operator's).
- Gate green: 371 backend + 3 pi-gated, 234 frontend, ruff/mypy
  clean, 95% backend coverage.
- 4 older relay worktrees from previous runs still around — left
  alone, not asked to sweep.
