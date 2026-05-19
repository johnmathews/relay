# 2026-05-19 — Phase 0: scaffold

First implementation day. Phase 0 from `docs/plan.md`: a pip-installable
`relay-v2` package with a runnable FastAPI app and the SQLite schema
created on first serve. Strictly scaffold — no harness, orchestrator,
REST surface, MCP, or dashboard (those are Phases 1–5).

## What was built

```
pyproject.toml                 uv project; entry-point `relay`; ruff + mypy config
src/relay_v2/
  __init__.py  version.py      package + single-source version (0.1.0)
  config.py                    pydantic-settings; RELAY_* env contract (spec §11)
  app.py                       FastAPI factory; /health; lifespan → init_db
  __main__.py                  argparse CLI: `relay serve`, `relay --version`
  py.typed                     PEP 561 marker (mypy strict consumes the package)
  db/__init__.py               make_engine / init_db (create_all on first serve)
  db/models.py                 SQLAlchemy 2.0 models — faithful port of spec §3.1
  db/migrations/__init__.py    placeholder; hand-rolled strategy per ADR-17
tests/test_smoke.py            4 tests: /health, db+schema, version, CLI --version
```

README gained a Development quickstart and a Phase 0 status; CLAUDE.md's
"Current state" and toolchain sections were updated (the file mandates
keeping them accurate after Phase 0).

## Verification (all of plan.md Phase 0's criteria)

- `uv sync && uv run pytest` → 4 passed.
- `uv run relay serve` → daemon up on `127.0.0.1:7800`.
- `curl http://127.0.0.1:7800/health` → `{"status":"ok"}`.
- First serve created `.relay/relay.db` (66 KB) with all six spec §3.1
  tables (`projects`, `users`, `prompts`, `runs`, `iters`, `events`),
  asserted by `test_first_serve_creates_db_with_schema`.
- Extra hygiene gate: `ruff check .` clean, `mypy` strict clean.

## Decisions

- **ADR-17 — hand-rolled `create_all` schema management for the MVP.**
  `plan.md` explicitly delegated "alembic or hand-rolled". Chose
  `create_all` at startup: the schema is greenfield, single-user,
  SQLite-backed, so a migration framework is premature. `db/migrations/`
  is reserved for the first numbered upgrade script; adopting Alembic
  later is additive and will get its own ADR. Recorded as a new ADR
  (decisions.md is append-only); spec.md §3.1 stays canonical and was
  not edited.
- **Schema fidelity.** `db/models.py` mirrors spec §3.1 exactly — a
  `ForeignKey` only where the spec's DDL has `REFERENCES`; `user_id`
  stays a plain defaulted column (reserved FK per ADR-12); JSON columns
  use SQLAlchemy's portable `JSON` type (clean Postgres path, ADR-11).
- **Synchronous engine for Phase 0.** Only schema creation is needed
  now; the async engine arrives with the orchestrator (Phase 2) and is
  encapsulated behind `relay_v2.db`. Noted in ADR-17 consequences.
- **`scratch/` excluded from ruff.** It is committed pi de-risking
  evidence (ground truth per CLAUDE.md), not project source, and must
  not be reformatted. Project source itself is lint-clean.

## Stayed in scope

No drift into Phase 1. The harness package, signaling, and pi invocation
were read for context (spec §4) but not implemented. `pi` was not
invoked or pinned — pi version pinning (OQ-5) is Phase 1 territory.

## Next

Phase 1 — harness layer: `Harness`/`HarnessSession`/`HarnessEvent`
protocol and `PiHarness`, with unit tests against the captured
`scratch/*.jsonl` fixtures and the `text_sentinels` parser ported from
v1.
