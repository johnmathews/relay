"""Hand-rolled schema migrations (ADR-17).

The MVP creates the schema via ``Base.metadata.create_all`` (see
``relay.db.init_db``). Alembic is deliberately deferred: the schema is
greenfield, single-user, and SQLite-backed, so a migration framework
would be pure overhead until the schema starts changing under live data.

When the first post-Phase-0 schema change lands, add a numbered module
here (``0001_<description>.py``) exposing ``upgrade(engine)`` /
``downgrade(engine)`` and a small runner. Adopting Alembic later remains
open — that switch will be recorded as its own ADR.
"""
