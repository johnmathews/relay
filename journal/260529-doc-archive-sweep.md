# 2026-05-29 — Doc sweep: archive shipped plans & proposals

Pass A of the post-MVP doc-freshness audit ran end-to-end this
afternoon. 15 closed phase plans + 3 superseded proposals (18 docs,
~610 KB of frozen planning history) moved out of `docs/plans/` and
`docs/proposals/` into `docs/archive/`, with inbound references in
active docs updated. Shipped as a single commit (`0f00835`) on top of
this session's earlier WCAG-AA contrast fix (`5924007`) and the
proposal-status sync (`e78444b`).

## Why

The repo entered MVP-acceptance-testing freeze on 2026-05-23 after
the fanout-join (9a–9g) and pause-for-review (14a–14f) arcs both
landed. By 2026-05-29 the active `docs/plans/` directory still
contained every plan from those arcs — 15 closed planning docs
mixed with zero in-flight ones — and `docs/proposals/` carried the
three proposals that drove those arcs, each still labelled
`**Status:** proposal (not yet ADR'd, not yet implemented)` despite
ADRs 33–46 having shipped the work.

This violated the `docs/archive/README.md` convention that closed
plans get a status header and a `git mv` to `archive/`. The cost was
incrementally invisible: the active doc listing was 75% shadow
inventory, a reader doing `ls docs/plans` would find a wall of
historical phase plans and no signal which (if any) were active, and
the canonical archive directory was sitting empty waiting for its
first occupant.

The trigger was a freshness-and-scope sweep requested mid-session.
The agent's report enumerated the 18 candidates plus three smaller
cleanups (one missing status header on
`harness-session-ended-persistence.md`, three stale `**Status:**`
lines on the proposals, one outdated paragraph in
`docs/archive/README.md`). The user approved Pass A; this is its
landing.

## What shipped

1. **18 `git mv` operations** flat into `docs/archive/`. No
   subdirectory mirroring (`archive/plans/`, `archive/proposals/`) —
   the archive is one bucket. Internal cross-references within
   archived docs (plan-to-plan, plan-to-proposal) use the old
   `docs/plans/...` / `docs/proposals/...` paths and were
   intentionally left unrewritten: archived docs are frozen
   history, not load-bearing live links. Same rule for
   `docs/decisions.md`: it's append-only ADR history, its existing
   path citations stay at their pre-archive form.

2. **One missing status header added** on
   `docs/archive/2026-05-22-harness-session-ended-persistence.md`
   (`**Status:** closed 2026-05-23 (shipped as Phase 9g; see
   CLAUDE.md §"Phase 9g" and ADR-39).`). The other 14 plans already
   carried their own status lines from when they were written.

3. **Three proposal status lines rewritten** from
   `**Status:** proposal (not yet ADR'd, not yet implemented)` to
   supersede-pointers naming the live canonical doc:
   - `parallel-iters-fanout-join.md` → `../fanout.md` (2026-05-23)
   - `pause-for-review.md` → `../spec.md` §6.2 + ADRs 40–41 (2026-05-23)
   - `skills-harness-variants.md` → `../skills.md` + ADR-33 + ADR-44 (2026-05-25)

4. **Inbound link rewrites in active docs:**
   - `CLAUDE.md` — 13 path references (every `docs/plans/2026-…` and
     two `docs/proposals/…` mentions) bulk-rewritten via `sed`.
     `docs/plans/` still appears once in line 8 as a directory-level
     reference ("Per-phase plans live in `docs/plans/`") — kept as
     forward-looking guidance for any future plan.
   - `docs/plan.md` — 2 references.
   - `docs/fanout.md` — 1 reference.
   - `docs/proposals/run-detail-layout.md` — 1 reference to its own
     Phase 1 plan.
   - `docs/decisions.md` — **intentionally untouched** (append-only).

5. **`docs/archive/README.md` updated** — the "Nothing is archived
   yet" placeholder paragraph replaced with a contents inventory
   grouped by category (proposals / phase plans) plus a footnote
   documenting the frozen-history convention for intra-archive
   cross-references.

## Verification & gate

- `grep -rn` for the old paths across active docs (CLAUDE.md +
  every `docs/*.md` except `decisions.md` and the archive itself)
  returned empty after the sweep — confirms no stale active-doc
  pointers remain.
- Code changes: zero. No Python, TypeScript, CSS, or test files
  touched. Backend (371 + 3 pi-e2e gated, 95% coverage) and
  frontend (375/375) gates carry forward unchanged from earlier in
  the session.
- CI ran on the rename-only commit and is in progress at the time
  of this entry; the gate has nothing executable to react to but
  the Docker rebuild kicks anyway.

## What I considered and rejected

1. **Mirror the old structure inside the archive**
   (`docs/archive/plans/...`, `docs/archive/proposals/...`). Would
   have preserved the symmetry but added a layer for no gain — the
   archive is a frozen-history bucket, not a parallel taxonomy, and
   inbound links from active docs needed rewriting either way.
   Flat won.
2. **Rewrite intra-archive cross-references.** Tempting for
   correctness but dangerous: 30+ touches across docs that exist
   precisely as decision history, and an edit anywhere is an
   invitation to slide into other edits. The `docs/archive/README.md`
   footnote now names this as an explicit convention.
3. **Edit `docs/decisions.md` path citations.** Same reasoning,
   stronger: the file is `append-only` by repo convention. Live
   inbound links from ADRs to plan/proposal docs stay at the
   pre-archive paths.
4. **Also archive `docs/proposals/run-detail-layout.md`.** Its
   status header now reads "All phases complete" after the
   contrast-fix update earlier in the session. Could have shipped
   in the same commit but I held it: it closed today, and one
   wrap-up cycle of buffer before moving it lets any
   ten-minute-later "actually one more thing" land without a churn
   commit. Will revisit next session.
5. **Pass B (spec.md §13 OQ-1…OQ-6 freshness audit) in this
   session.** Considered but pushed to a fresh session: that pass
   needs to load most of `decisions.md` (~3000 lines, 47 ADRs) +
   `spec.md` end-to-end, and stacking it on top of this session's
   contrast-fix + archive-sweep context risks reading-fatigue
   imprecision on judgment calls about which OQs are closed. Wrote
   a self-contained prompt for the new session, user copied it
   out.

## What did NOT change

- `docs/decisions.md` (append-only; intra-ADR path citations
  intentionally frozen).
- Intra-archive cross-references inside the now-archived plans and
  proposals (frozen history; convention documented in
  `docs/archive/README.md`).
- The active proposal `docs/proposals/run-detail-layout.md` (still
  carries "All phases complete" but kept active for a cycle).
- All 14 canonical `docs/` reference docs (spec, motivation, plan,
  harness, orchestrator, api, dashboard, mcp, skills, observability,
  fanout, getting-started, acceptance-testing, decisions). None had
  drift worth fixing in this pass — only the inbound paths.
- Backend, frontend, sentinel parser, SSE wire shape, OTel pipeline,
  event-store invariants. Pure doc reorganization.

## Cross-cutting note for future sessions

The repo convention is now demonstrated end-to-end: planning docs
get a status header at write time, get a supersede/closed line when
they land, and `git mv` to `docs/archive/` flat. CLAUDE.md path
references and the README inventory get updated in the same commit.
`docs/decisions.md` and intra-archive links stay at their original
paths as deliberate frozen history. If a future planning doc lands
without a status header, the `harness-session-ended-persistence`
add-header-then-archive pattern is the template.

The active proposal directory now holds exactly one doc
(`run-detail-layout.md`). When the next post-MVP arc kicks off the
proposal goes there fresh; when it closes, this archive sweep is
the recipe.
