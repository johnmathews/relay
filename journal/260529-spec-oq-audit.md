# 2026-05-29 — Spec §13 open-questions audit

Small follow-on to the morning's doc-archive sweep: audited
`docs/spec.md` §13 ("Open questions" OQ-1…OQ-6) against the post-MVP
ADR log (47 ADRs) and the Phase 9*/14* / post-MVP blocks in
`CLAUDE.md`, then applied two edits.

## What changed

- **OQ-3** — was *Partially answered (ADR-18)* with the OTel per-iter
  aggregation flagged as the open half. Phase 7 / ADR-29 closed that
  half (`docs/decisions.md:1379–1395`; see also `docs/spec.md` §10
  Phase-7 implementation note around lines 941–960): GenAI usage
  attributes are set on the `relay.iter` span by summing
  `SessionEnded.messages[].usage` across assistant messages, cache/
  cost under `relay.usage.*`, missing fields omitted not zero-filled.
  Rewrote the OQ-3 bullet to mark it fully resolved citing ADR-18 +
  ADR-29.
- **OQ-6** (pi `auth.json` refresh — does relay monitor expiration,
  or does pi handle silently?) — **left open**. No ADR formally
  closes it. ADR-09 stays *Provisional*. But MVP shipped through
  Phases 0–8 + 9a–9g + 14a–14f with zero relay-side auth-monitoring
  code, which is consistent with "pi handles silently." Tightened
  the bullet to record that empirical state without claiming formal
  closure.

OQ-1, OQ-2, OQ-4, OQ-5 were already marked resolved with ADR
citations; no change.

## Tag-clash note (for future readers)

`CLAUDE.md` cites "OQ-N" tags in the 9b/9c fanout and 14c/14e/14f
pause-for-review contexts (e.g. "OQ-1 status-quo on join_prompt",
"OQ-3 missing-file create flow"). Those are scoped to each
proposal's own Open Questions section in `docs/archive/`
(parallel-iters-fanout-join.md, pause-for-review.md) — they are
**not** spec.md §13 numbers. Spec §13's OQ-1…OQ-6 is the
MVP-time list carried from `motivation.md` risks. Distinct lists,
overlapping numbering — easy to confuse in audits.

## Scope guardrails

`docs/decisions.md` is append-only; the audit deliberately proposed
zero ADR edits. The only file touched was `docs/spec.md` §13. No
companion edit was required elsewhere in spec.md (OQ-3's resolution
is already fully documented in §10's Phase-7 note; the new §13 text
just back-points to it).
