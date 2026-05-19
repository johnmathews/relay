"""Database bootstrap.

Two engines, one schema, one SQLite file (ADR-17, ADR-21):

- A **sync** engine creates the schema once at startup (``init_db``).
  Phase 0 only ever needed this.
- An **async** engine (``aiosqlite``) backs every orchestrator-driven
  read/write so DB work never blocks the asyncio event loop. It arrives
  with the orchestrator (Phase 2) and stays encapsulated behind this
  module — nothing above ``relay_v2.db`` constructs an engine.

``db/migrations/`` holds future versioned scripts; the SQLAlchemy models
in ``models.py`` remain the schema source of truth (a faithful port of
spec.md §3.1, which stays canonical).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from relay_v2.config import Settings
from relay_v2.db.models import Base

__all__ = [
    "Base",
    "make_engine",
    "init_db",
    "make_async_engine",
    "make_async_sessionmaker",
]


def make_engine(db_url: str) -> Engine:
    """Create a SQLite engine usable from FastAPI's worker threads."""
    return create_engine(db_url, connect_args={"check_same_thread": False})


def make_async_engine(async_db_url: str) -> AsyncEngine:
    """Create the async (``aiosqlite``) engine for orchestrator I/O.

    A 30s busy timeout absorbs the brief writer contention SQLite can see
    when several runs append events concurrently (single-writer file;
    EventStore also serialises its own appends with a lock)."""
    return create_async_engine(
        async_db_url, connect_args={"timeout": 30}
    )


def make_async_sessionmaker(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the async engine.

    ``expire_on_commit=False`` keeps ORM instances usable after commit
    without re-querying — the orchestrator reads fields off rows it just
    wrote within the same iter.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


def init_db(settings: Settings) -> Engine:
    """Ensure the data dir and the SQLite schema exist; return the engine.

    Idempotent: ``create_all`` only creates missing tables, so repeated
    ``relay serve`` invocations are safe.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    Base.metadata.create_all(engine)
    return engine
