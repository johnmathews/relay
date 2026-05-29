# docs/archive

Superseded or closed documents live here so the active `docs/` listing
stays easy to scan while decision history is preserved.

**Convention.** When a doc is superseded or closed, add a status header
at its top —

```
**Status:** superseded by [new-doc.md](../new-doc.md) (YYYY-MM-DD).
```

or `**Status:** closed YYYY-MM-DD.` — then `git mv` it into this
directory and update inbound links from any active docs.

Do **not** edit ADRs this way: `docs/decisions.md` is append-only; a
superseded ADR gets a `**Status:** superseded by ADR-NN` header in place
and a new ADR is appended (see `docs/decisions.md`).

## Contents

Archived 2026-05-29 as part of the post-MVP doc sweep:

**Shipped proposals** (superseded by their canonical active docs):

- `parallel-iters-fanout-join.md` → shipped as Phases 9a–9g; live ref is [`../fanout.md`](../fanout.md).
- `pause-for-review.md` → shipped as Phases 14a–14f; live ref is [`../spec.md`](../spec.md) §6.2.
- `skills-harness-variants.md` → shipped as ADR-33 + ADR-44; live ref is [`../skills.md`](../skills.md).

**Closed phase plans:**

- `2026-05-21-fanout-join-9a.md` … `9f.md` (`2026-05-22-fanout-join-9f.md`) — fanout-join arc.
- `2026-05-22-harness-session-ended-persistence.md` — Phase 9g (closes the latent ADR-10 gap, ADR-39).
- `2026-05-21-skill-variants.md` — shipped as ADR-33 + ADR-44.
- `2026-05-22-pause-for-review-14a.md` … `14f.md` (`2026-05-23-pause-for-review-14f.md`) — pause-for-review arc.
- `2026-05-28-run-detail-layout-shell.md` — Phase 1 of the still-active `../proposals/run-detail-layout.md`.

Internal cross-references within these archived docs (plan-to-plan,
plan-to-proposal) use repo-relative `docs/plans/...` / `docs/proposals/...`
paths from when the docs were active and are intentionally left
unrewritten — they're frozen history, not load-bearing links.
`docs/decisions.md` (append-only ADRs) similarly retains its
pre-archive path citations.
