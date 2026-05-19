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

Nothing is archived yet — this directory establishes the machinery
before it is first needed.
